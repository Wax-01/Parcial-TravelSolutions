"""Cars service: rental reservations with idempotent reserve/cancel (SAGA participant)."""
import logging
from contextlib import asynccontextmanager
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from wandersync_common.db import make_pool
from wandersync_common.internal import apply_simulated_failure, require_internal

logging.basicConfig(level=logging.INFO, format="%(asctime)s cars %(levelname)s %(message)s")
log = logging.getLogger("cars")
pool = make_pool(max_size=2)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await pool.open()
    yield
    await pool.close()


app = FastAPI(title="WanderSync Cars", lifespan=lifespan)


class ReserveRequest(BaseModel):
    order_id: UUID
    car_id: UUID
    days: int = Field(ge=1, le=60)


@app.get("/healthz")
async def healthz():
    async with pool.connection() as conn:
        await conn.execute("select 1")
    return {"status": "ok"}


@app.post("/reservations", dependencies=[Depends(require_internal)])
async def reserve(body: ReserveRequest, x_simulate_failure: str | None = Header(default=None)):
    await apply_simulated_failure(x_simulate_failure, f"reserve:{body.order_id}")
    async with pool.connection() as conn:
        async with conn.transaction():
            existing = await (await conn.execute(
                "select id, status, days from wandersync.car_reservations where order_id=%s for update",
                (body.order_id,))).fetchone()
            if existing:
                if existing["status"] != "CONFIRMED":
                    raise HTTPException(409, "reservation already cancelled for this order")
                car = await (await conn.execute(
                    "select price_per_day from wandersync.cars where id=%s", (body.car_id,))).fetchone()
                return {"reservation_id": str(existing["id"]),
                        "amount": str(car["price_per_day"] * existing["days"]), "idempotent": True}
            car = await (await conn.execute(
                "update wandersync.cars set units_available = units_available - 1 "
                "where id=%s and units_available >= 1 returning price_per_day", (body.car_id,))).fetchone()
            if not car:
                raise HTTPException(409, "car not found or unavailable")
            row = await (await conn.execute(
                "insert into wandersync.car_reservations(order_id, car_id, days) "
                "values (%s,%s,%s) returning id", (body.order_id, body.car_id, body.days))).fetchone()
    log.info("car reserved order=%s reservation=%s", body.order_id, row["id"])
    return {"reservation_id": str(row["id"]),
            "amount": str(car["price_per_day"] * body.days), "idempotent": False}


@app.delete("/reservations/{order_id}", dependencies=[Depends(require_internal)])
async def cancel(order_id: UUID, x_simulate_failure: str | None = Header(default=None)):
    """Compensation. Idempotent and tolerant: cancelling a never-made reservation is a no-op."""
    await apply_simulated_failure(x_simulate_failure, f"cancel:{order_id}")
    async with pool.connection() as conn:
        async with conn.transaction():
            res = await (await conn.execute(
                "select id, car_id, status from wandersync.car_reservations where order_id=%s for update",
                (order_id,))).fetchone()
            if not res:
                return {"status": "NOT_FOUND"}
            if res["status"] == "CANCELLED":
                return {"status": "ALREADY_CANCELLED"}
            await conn.execute(
                "update wandersync.car_reservations set status='CANCELLED', cancelled_at=now() where id=%s",
                (res["id"],))
            await conn.execute(
                "update wandersync.cars set units_available = units_available + 1 where id=%s", (res["car_id"],))
    log.info("car reservation cancelled order=%s", order_id)
    return {"status": "CANCELLED"}
