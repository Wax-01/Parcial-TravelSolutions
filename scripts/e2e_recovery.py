"""Crash recovery: kill the SAGA orchestrator mid-flight and verify the order is rolled back automatically.

    python scripts/e2e_recovery.py      (takes ~2.5 min: orphaned PENDING orders are reaped after 120 s)
"""
import subprocess
import sys
import time
import uuid

sys.path.insert(0, __file__.rsplit("scripts", 1)[0] + "scripts")
from e2e import Sess, db_conn, gql, check, results  # noqa: E402


def main() -> int:
    s = Sess()
    email, pw = f"rec-{uuid.uuid4().hex[:8]}@example.com", "correct-horse-battery"
    gql(s, 'mutation($e:String!,$p:String!){ register(email:$e,password:$p){ __typename } }', {"e": email, "p": pw})
    day = time.strftime("%Y-%m-%d", time.gmtime(time.time() + 4 * 86400))
    pk = gql(s, """query($d:String!){ searchPackages(origin:"BOG", destination:"CTG", departDate:$d, first:1){
        flights{edges{node{id}}} hotels{edges{node{id}}} cars{edges{node{id}}} } }""", {"d": day})["data"]["searchPackages"]
    ids = [pk[k]["edges"][0]["node"]["id"] for k in ("flights", "hotels", "cars")]
    res = gql(s, "mutation($i:CreateBookingInput!){ createBooking(input:$i){ ... on BookingAccepted{ orderId } } }",
              {"i": {"flightId": ids[0], "hotelId": ids[1], "carId": ids[2], "nights": 1}})["data"]["createBooking"]
    order_id = res["orderId"]
    time.sleep(1.2)  # flight (and maybe hotel) reserved, saga still running (700 ms pause between steps)
    subprocess.run(["docker", "compose", "restart", "-t", "0", "orders"], check=True, capture_output=True)
    print("orders restarted mid-saga; waiting for the reaper...")
    conn = db_conn()
    status = None
    for _ in range(60):
        time.sleep(5)
        status = conn.execute("select status from wandersync.orders where id=%s", (order_id,)).fetchone()[0]
        if status in ("CANCELLED", "COMPENSATION_FAILED", "CONFIRMED"):
            break
    check("orphaned saga was rolled back automatically after orchestrator crash", status == "CANCELLED", f"status={status}")
    left = {t: [r[0] for r in conn.execute(f"select status from wandersync.{t} where order_id=%s", (order_id,)).fetchall()]
            for t in ("flight_reservations", "hotel_reservations", "car_reservations", "payments")}
    ok = not any("CONFIRMED" in v or "CAPTURED" in v for v in left.values())
    check("no active reservations or payments left", ok, str(left))
    return 0 if all(r[1] for r in results) else 1


if __name__ == "__main__":
    sys.exit(main())
