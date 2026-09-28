"""Pure clean/normalize + batch upsert logic (unit-testable, no Prefect/Dask imports)."""
import re
from typing import Any

import psycopg

from wandersync_common.db import conninfo

IATA = re.compile(r"^[A-Z]{3}$")

# Upserts refresh prices/metadata but NEVER overwrite live inventory (seats/rooms/units) that
# reservations may already have decremented.
UPSERT = {
    "flights": (
        "insert into wandersync.flights(source, external_id, airline, origin, destination, departure_at, "
        "arrival_at, price, currency, seats_available, fetched_at) values (%(source)s, %(external_id)s, "
        "%(airline)s, %(origin)s, %(destination)s, %(departure_at)s, %(arrival_at)s, %(price)s, %(currency)s, "
        "%(seats_available)s, now()) on conflict (source, external_id) do update set airline=excluded.airline, "
        "departure_at=excluded.departure_at, arrival_at=excluded.arrival_at, price=excluded.price, fetched_at=now()"),
    "hotels": (
        "insert into wandersync.hotels(source, external_id, name, city, stars, price_per_night, currency, "
        "rooms_available, fetched_at) values (%(source)s, %(external_id)s, %(name)s, %(city)s, %(stars)s, "
        "%(price_per_night)s, %(currency)s, %(rooms_available)s, now()) on conflict (source, external_id) "
        "do update set name=excluded.name, stars=excluded.stars, price_per_night=excluded.price_per_night, "
        "fetched_at=now()"),
    "cars": (
        "insert into wandersync.cars(source, external_id, provider, model, category, city, price_per_day, "
        "currency, units_available, fetched_at) values (%(source)s, %(external_id)s, %(provider)s, %(model)s, "
        "%(category)s, %(city)s, %(price_per_day)s, %(currency)s, %(units_available)s, now()) "
        "on conflict (source, external_id) do update set provider=excluded.provider, model=excluded.model, "
        "category=excluded.category, price_per_day=excluded.price_per_day, fetched_at=now()"),
}
PRICE_FIELD = {"flights": "price", "hotels": "price_per_night", "cars": "price_per_day"}
STRING_FIELDS = ("source", "external_id", "airline", "origin", "destination", "name", "city", "provider",
                 "model", "category", "currency")


def clean(kind: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize strings, drop invalid rows, de-duplicate by (source, external_id)."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    price_key = PRICE_FIELD[kind]
    for rec in records:
        rec = dict(rec)
        for field in STRING_FIELDS:
            if isinstance(rec.get(field), str):
                rec[field] = " ".join(rec[field].split())
        if kind == "flights":
            rec["origin"], rec["destination"] = rec["origin"].upper(), rec["destination"].upper()
            if not (IATA.match(rec["origin"]) and IATA.match(rec["destination"])):
                continue
            if rec["arrival_at"] <= rec["departure_at"]:
                continue
        else:
            rec["city"] = rec["city"].upper()
            if not IATA.match(rec["city"]):
                continue
        if not rec.get(price_key) or rec[price_key] <= 0:
            continue
        out[(rec["source"], rec["external_id"])] = rec
    return list(out.values())


def upsert(kind: str, records: list[dict[str, Any]]) -> int:
    if not records:
        return 0
    with psycopg.connect(conninfo(), prepare_threshold=None, connect_timeout=15) as conn:
        with conn.cursor() as cur:
            cur.executemany(UPSERT[kind], records)
        conn.commit()
    return len(records)


def record_run(flow_run: str, summary: dict[str, int], status: str) -> None:
    with psycopg.connect(conninfo(), prepare_threshold=None, connect_timeout=15) as conn:
        for kind, rows in summary.items():
            conn.execute(
                "insert into wandersync.ingestion_runs(flow_run, source, kind, rows_upserted, status) "
                "values (%s,%s,%s,%s,%s)", (flow_run, "multi", kind, rows, status))
        conn.commit()
