"""Flights service: seat reservations with idempotent reserve/cancel (SAGA participant)."""
import logging
from contextlib import asynccontextmanager
from uuid import UUID

import psycopg.errors
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from wandersync_common.db import make_pool
from wandersync_common.internal import apply_simulated_failure, require_internal

logging.basicConfig(level=logging.INFO, format="%(asctime)s flights %(levelname)s %(message)s")
log = logging.getLogger("flights")
pool = make_pool(max_size=2)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await pool.open()
    yield
    await pool.close()


app = FastAPI(title="WanderSync Flights", lifespan=lifespan)


class ReserveRequest(BaseModel):
    order_id: UUID
    flight_id: UUID
    seats: int = Field(default=1, ge=1, le=9)


@app.get("/healthz")
async def healthz():
    async with pool.connection() as conn:
        await conn.execute("select 1")
    return {"status": "ok"}


@app.post("/reservations", dependencies=[Depends(require_internal)])
async def reserve(body: ReserveRequest, x_simulate_failure: str | None = Header(default=None)):
    await apply_simulated_failure(x_simulate_failure, f"reserve:{body.order_id}")
    try:
        async with pool.connection() as conn:
            async with conn.transaction():
                existing = await (await conn.execute(
                    "select id, status, seats from wandersync.flight_reservations where order_id=%s for update",
                    (body.order_id,))).fetchone()
                if existing:
                    if existing["status"] != "CONFIRMED":
                        raise HTTPException(409, "order already compensated")
                    flight = await (await conn.execute(
                        "select price from wandersync.flights where id=%s", (body.flight_id,))).fetchone()
                    return {"reservation_id": str(existing["id"]),
                            "amount": str(flight["price"] * existing["seats"]), "idempotent": True}
                flight = await (await conn.execute(
                    "update wandersync.flights set seats_available = seats_available - %s "
                    "where id=%s and seats_available >= %s returning price",
                    (body.seats, body.flight_id, body.seats))).fetchone()
                if not flight:
                    raise HTTPException(409, "flight not found or sold out")
                row = await (await conn.execute(
                    "insert into wandersync.flight_reservations(order_id, flight_id, seats) "
                    "values (%s,%s,%s) returning id", (body.order_id, body.flight_id, body.seats))).fetchone()
    except psycopg.errors.UniqueViolation:
        # A compensation already ran for this order (tombstone): late reservation rejected.
        raise HTTPException(409, "order already compensated")
    log.info("flight reserved order=%s reservation=%s", body.order_id, row["id"])
    return {"reservation_id": str(row["id"]), "amount": str(flight["price"] * body.seats), "idempotent": False}


@app.delete("/reservations/{order_id}", dependencies=[Depends(require_internal)])
async def cancel(order_id: UUID, x_simulate_failure: str | None = Header(default=None)):
    """Compensation. Idempotent and tolerant: cancelling a never-made reservation is a no-op."""
    await apply_simulated_failure(x_simulate_failure, f"cancel:{order_id}")
    async with pool.connection() as conn:
        async with conn.transaction():
            res = await (await conn.execute(
                "select id, flight_id, seats, status from wandersync.flight_reservations "
                "where order_id=%s for update", (order_id,))).fetchone()
            if not res:
                # Tombstone: a reserve request that is still in flight (timeout) must not succeed later.
                await conn.execute(
                    "insert into wandersync.flight_reservations(order_id, flight_id, seats, status, cancelled_at) "
                    "select id, flight_id, 1, 'CANCELLED', now() from wandersync.orders where id=%s "
                    "on conflict (order_id) do nothing", (order_id,))
                return {"status": "NOT_FOUND"}
            if res["status"] == "CANCELLED":
                return {"status": "ALREADY_CANCELLED"}
            await conn.execute(
                "update wandersync.flight_reservations set status='CANCELLED', cancelled_at=now() where id=%s",
                (res["id"],))
            await conn.execute(
                "update wandersync.flights set seats_available = seats_available + %s where id=%s",
                (res["seats"], res["flight_id"]))
    log.info("flight reservation cancelled order=%s", order_id)
    return {"status": "CANCELLED"}
