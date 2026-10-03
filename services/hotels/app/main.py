"""Hotels service: room reservations with idempotent reserve/cancel (SAGA participant)."""
import logging
from contextlib import asynccontextmanager
from uuid import UUID

import psycopg.errors
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from wandersync_common.db import make_pool
from wandersync_common.internal import apply_simulated_failure, require_internal

logging.basicConfig(level=logging.INFO, format="%(asctime)s hotels %(levelname)s %(message)s")
log = logging.getLogger("hotels")
pool = make_pool(max_size=2)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await pool.open()
    yield
    await pool.close()


app = FastAPI(title="WanderSync Hotels", lifespan=lifespan)


class ReserveRequest(BaseModel):
    order_id: UUID
    hotel_id: UUID
    nights: int = Field(ge=1, le=60)


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
                    "select id, status, nights from wandersync.hotel_reservations where order_id=%s for update",
                    (body.order_id,))).fetchone()
                if existing:
                    if existing["status"] != "CONFIRMED":
                        raise HTTPException(409, "order already compensated")
                    hotel = await (await conn.execute(
                        "select price_per_night from wandersync.hotels where id=%s", (body.hotel_id,))).fetchone()
                    return {"reservation_id": str(existing["id"]),
                            "amount": str(hotel["price_per_night"] * existing["nights"]), "idempotent": True}
                hotel = await (await conn.execute(
                    "update wandersync.hotels set rooms_available = rooms_available - 1 "
                    "where id=%s and rooms_available >= 1 returning price_per_night", (body.hotel_id,))).fetchone()
                if not hotel:
                    raise HTTPException(409, "hotel not found or no rooms available")
                row = await (await conn.execute(
                    "insert into wandersync.hotel_reservations(order_id, hotel_id, nights) "
                    "values (%s,%s,%s) returning id", (body.order_id, body.hotel_id, body.nights))).fetchone()
    except psycopg.errors.UniqueViolation:
        # A compensation already ran for this order (tombstone): late reservation rejected.
        raise HTTPException(409, "order already compensated")
    log.info("hotel reserved order=%s reservation=%s", body.order_id, row["id"])
    return {"reservation_id": str(row["id"]),
            "amount": str(hotel["price_per_night"] * body.nights), "idempotent": False}


@app.delete("/reservations/{order_id}", dependencies=[Depends(require_internal)])
async def cancel(order_id: UUID, x_simulate_failure: str | None = Header(default=None)):
    """Compensation. Idempotent and tolerant: cancelling a never-made reservation is a no-op."""
    await apply_simulated_failure(x_simulate_failure, f"cancel:{order_id}")
    async with pool.connection() as conn:
        async with conn.transaction():
            res = await (await conn.execute(
                "select id, hotel_id, status from wandersync.hotel_reservations where order_id=%s for update",
                (order_id,))).fetchone()
            if not res:
                # Tombstone: a reserve request that is still in flight (timeout) must not succeed later.
                await conn.execute(
                    "insert into wandersync.hotel_reservations(order_id, hotel_id, nights, status, cancelled_at) "
                    "select id, hotel_id, nights, 'CANCELLED', now() from wandersync.orders where id=%s "
                    "on conflict (order_id) do nothing", (order_id,))
                return {"status": "NOT_FOUND"}
            if res["status"] == "CANCELLED":
                return {"status": "ALREADY_CANCELLED"}
            await conn.execute(
                "update wandersync.hotel_reservations set status='CANCELLED', cancelled_at=now() where id=%s",
                (res["id"],))
            await conn.execute(
                "update wandersync.hotels set rooms_available = rooms_available + 1 where id=%s",
                (res["hotel_id"],))
    log.info("hotel reservation cancelled order=%s", order_id)
    return {"status": "CANCELLED"}
