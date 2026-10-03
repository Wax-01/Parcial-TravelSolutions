"""End-to-end checks against the running stack (through the same GraphQL endpoint the frontend uses).

    python scripts/e2e.py            # needs `pip install httpx psycopg[binary]` and the stack up on :8080

Verifies: Session Fixation defence, SAGA happy path, compensation for each simulated failure
(and that NO orphan reservations/payments remain in the database), and rate limiting (HTTP 429).
"""
import os
import sys
import time
import uuid

import httpx

BASE = os.environ.get("BASE_URL", "http://localhost:8080")
URL = f"{BASE}/graphql"
FIELDS_BOOKING = "__typename ... on BookingAccepted { orderId status } ... on ApiError { code message }"
results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"[{'PASS' if ok else 'FAIL'}] {name} {detail}")


class Sess:
    """Minimal client with manual cookie handling (explicit session id -> easy to demo Session Fixation)."""

    def __init__(self, sid: str | None = None):
        self.sid = sid
        self.headers: dict[str, str] = {}

    def post(self, url: str, json: dict) -> httpx.Response:
        headers = {"cookie": f"ws_session={self.sid}"} if self.sid else {}
        r = httpx.post(url, json=json, headers=headers, timeout=30)
        if r.cookies.get("ws_session"):
            self.sid = r.cookies.get("ws_session")
        return r


def gql(client: Sess, query: str, variables=None) -> dict:
    for _ in range(3):
        r = client.post(URL, json={"query": query, "variables": variables or {}})
        if r.status_code == 429:
            wait = int(r.headers.get("retry-after", "5")) + 1
            print(f"   (429 rate limited, waiting {wait}s)")
            time.sleep(wait)
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("still rate limited")


def db_conn():
    try:
        import psycopg
        env = dict(l.strip().split("=", 1) for l in open(".env", encoding="utf-8") if "=" in l and not l.startswith("#"))
        from urllib.parse import urlsplit
        url = env["DATABASE_URL"].strip().strip('"')
        parts = urlsplit(url)
        if "pooler.supabase.com" in (parts.hostname or "") and parts.port == 5432:
            url = url.replace(":5432/", ":6543/")
        return psycopg.connect(url, prepare_threshold=None, autocommit=True)
    except Exception as exc:  # DB checks are optional
        print("   (db verification skipped:", type(exc).__name__, ")")
        return None


def wait_order(client: Sess, order_id: str, timeout: int = 60) -> dict:
    q = "query($id: UUID!) { order(id: $id) { id status totalAmount failureReason steps { step action status detail } } }"
    end = time.time() + timeout
    while time.time() < end:
        order = gql(client, q, {"id": order_id})["data"]["order"]
        if order["status"] in ("CONFIRMED", "CANCELLED", "COMPENSATION_FAILED"):
            return order
        time.sleep(0.5)
    raise TimeoutError(order_id)


def main() -> int:
    client = Sess()

    # ---------------------------------------------------------- Session Fixation
    gql(client, "{ me { id } }")
    email = f"e2e-{uuid.uuid4().hex[:8]}@example.com"
    pw = "correct-horse-battery"
    r = gql(client, 'mutation($e:String!,$p:String!){ register(email:$e,password:$p){ __typename ... on ApiError{code} } }',
            {"e": email, "p": pw})
    check("register creates user", r["data"]["register"]["__typename"] == "User")
    attacker = Sess()
    gql(attacker, "{ me { id } }")
    fixed = attacker.sid                                  # session id known to the attacker
    victim = Sess(fixed)                                  # ...and planted in the victim's browser
    r = gql(victim, 'mutation($e:String!,$p:String!){ login(email:$e,password:$p){ __typename } }', {"e": email, "p": pw})
    new_id = victim.sid
    check("login succeeds", r["data"]["login"]["__typename"] == "User")
    check("session id REGENERATED on login (anti fixation)", new_id and new_id != fixed)
    attacker.sid = fixed
    check("pre-login session id no longer authenticates", gql(attacker, "{ me { id } }")["data"]["me"] is None)
    check("new session id authenticates", gql(victim, "{ me { email } }")["data"]["me"]["email"] == email)
    client = victim

    # ---------------------------------------------------------- catalogue via pg_graphql (no over-fetching)
    day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 3 * 86400))
    search = """query($d:String!){ searchPackages(origin:"BOG", destination:"MDE", departDate:$d, nights:2, first:30){
        flights{ edges{ node{ id price seatsAvailable } } } hotels{ edges{ node{ id roomsAvailable } } }
        cars{ edges{ node{ id unitsAvailable } } } } }"""
    pk = gql(client, search, {"d": day})["data"]["searchPackages"]
    check("searchPackages returns flights+hotels+cars", all(pk[k]["edges"] for k in ("flights", "hotels", "cars")))
    check("only requested fields returned", set(pk["flights"]["edges"][0]["node"]) == {"id", "price", "seatsAvailable"})

    # Each run leaves one confirmed booking holding inventory (real items have few units): pick the best-stocked
    # items so repeated runs never hit "sold out" (which the SAGA would also compensate, but changes the scenario).
    def best(kind: str, stock: str) -> str:
        return max(pk[kind]["edges"], key=lambda e: e["node"][stock])["node"]["id"]

    ids = {"flight": best("flights", "seatsAvailable"), "hotel": best("hotels", "roomsAvailable"),
           "car": best("cars", "unitsAvailable")}

    conn = db_conn()

    def inventory():
        if not conn:
            return None
        f = conn.execute("select seats_available from wandersync.flights where id=%s", (ids["flight"],)).fetchone()[0]
        h = conn.execute("select rooms_available from wandersync.hotels where id=%s", (ids["hotel"],)).fetchone()[0]
        c = conn.execute("select units_available from wandersync.cars where id=%s", (ids["car"],)).fetchone()[0]
        return f, h, c

    def book(failure=None) -> dict:
        m = f"mutation($i:CreateBookingInput!){{ createBooking(input:$i){{ {FIELDS_BOOKING} }} }}"
        inp = {"flightId": ids["flight"], "hotelId": ids["hotel"], "carId": ids["car"], "nights": 2,
               "idempotencyKey": uuid.uuid4().hex, "simulateFailure": failure}
        res = gql(client, m, {"i": inp})["data"]["createBooking"]
        assert res["__typename"] == "BookingAccepted", res
        return wait_order(client, res["orderId"])

    def db_state(order_id):
        if not conn:
            return None
        one = lambda t: [r[0] for r in conn.execute(f"select status from wandersync.{t} where order_id=%s", (order_id,)).fetchall()]
        return {"flight": one("flight_reservations"), "hotel": one("hotel_reservations"),
                "car": one("car_reservations"), "payment": one("payments")}

    # ---------------------------------------------------------- SAGA
    base = inventory()
    o = book()
    check("SAGA happy path -> CONFIRMED", o["status"] == "CONFIRMED", f"total={o['totalAmount']}")
    st = db_state(o["id"])
    if st:
        check("happy path: 3 reservations + payment CONFIRMED/CAPTURED in DB",
              st == {"flight": ["CONFIRMED"], "hotel": ["CONFIRMED"], "car": ["CONFIRMED"], "payment": ["CAPTURED"]}, str(st))

    scenarios = [
        ("CAR", "car fails -> hotel and flight cancelled", {"flight": ["CANCELLED"], "hotel": ["CANCELLED"], "car": [], "payment": []}),
        ("HOTEL", "hotel fails -> flight cancelled", {"flight": ["CANCELLED"], "hotel": [], "car": [], "payment": []}),
        ("PAYMENT", "payment fails -> car, hotel, flight cancelled",
         {"flight": ["CANCELLED"], "hotel": ["CANCELLED"], "car": ["CANCELLED"], "payment": []}),
        # The car compensation finds nothing yet and leaves a CANCELLED tombstone, so the reserve request that
        # is still "hanging" in the car service gets rejected when it finally arrives (checked below).
        ("CAR_TIMEOUT", "car timeout (ambiguous) -> everything cancelled",
         {"flight": ["CANCELLED"], "hotel": ["CANCELLED"], "car": ["CANCELLED"], "payment": []}),
        ("CAR_FLAKY_COMPENSATION", "compensation fails twice, retried automatically -> cancelled",
         {"flight": ["CANCELLED"], "hotel": ["CANCELLED"], "car": [], "payment": []}),
    ]
    late = None  # (order id, time) of the timed-out car reservation that will arrive ~60 s later
    for code, label, expected in scenarios:
        started = time.time()
        o = book(code)
        if code == "CAR_TIMEOUT":
            late = (o["id"], started)
        check(f"SAGA {code}: {label}", o["status"] == "CANCELLED", f"reason={o['failureReason']}")
        st = db_state(o["id"])
        if st:
            check(f"SAGA {code}: DB consistent (no orphan reservations)", st == expected, str(st))
        if code == "CAR_FLAKY_COMPENSATION":
            failed = [s for s in o["steps"] if s["action"] == "COMPENSATE" and s["status"] == "FAILED"]
            check("flaky compensation was retried (2 failed attempts logged)", len(failed) == 2, f"{len(failed)} failures")
    after = inventory()
    if base and after:
        # only the confirmed booking should hold inventory
        check("inventory restored after compensations (only happy path holds 1 seat/room/car)",
              after == (base[0] - 1, base[1] - 1, base[2] - 1), f"{base} -> {after}")

    # ---------------------------------------------------------- Rate limiting
    codes = []
    rl = Sess()
    for _ in range(8):
        r = rl.post(URL, json={"query": 'mutation{ login(email:"nobody@example.com", password:"wrong-password-1"){ __typename } }'})
        codes.append(r.status_code)
    check("rate limit on login -> HTTP 429 with Retry-After", 429 in codes and r.headers.get("retry-after") is not None, str(codes))

    # ---------------------------------------------------------- late reservation after compensation
    if conn and late:
        order_id, started = late
        wait = max(0, started + 66 - time.time())  # simulated hang is 60 s inside the car service
        print(f"   (waiting {wait:.0f}s for the timed-out car reservation to arrive late)")
        time.sleep(wait)
        st = db_state(order_id)
        final = inventory()
        check("late car reservation after compensation is rejected (no orphan)",
              st["car"] == ["CANCELLED"] and final[2] == base[2] - 1, f"car={st['car']} units {base[2]} -> {final[2]}")

    failed = [n for n, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
