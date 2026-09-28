"""Postgres persistence for orders, saga log and payments."""
from decimal import Decimal
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool


class PgStore:
    def __init__(self, pool: AsyncConnectionPool):
        self.pool = pool

    # ---- SagaStore protocol -------------------------------------------------
    async def log(self, order_id: UUID, step: str, action: str, status: str, detail: str | None = None) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "insert into wandersync.saga_log(order_id, step, action, status, detail) values (%s,%s,%s,%s,%s)",
                (order_id, step, action, status, detail))

    async def set_status(self, order_id: UUID, status: str, **fields: Any) -> None:
        allowed = {"failure_reason", "total_amount"}
        sets, params = ["status=%s", "updated_at=now()"], [status]
        for key, value in fields.items():
            if key in allowed:
                sets.append(f"{key}=%s")
                params.append(value)
        params.append(order_id)
        async with self.pool.connection() as conn:
            await conn.execute(f"update wandersync.orders set {', '.join(sets)} where id=%s", params)

    # ---- orders -------------------------------------------------------------
    async def create_order(self, data: dict[str, Any]) -> tuple[dict[str, Any], bool]:
        """Returns (order, created). Idempotent on (user_id, idempotency_key)."""
        async with self.pool.connection() as conn:
            async with conn.transaction():
                row = await (await conn.execute(
                    "insert into wandersync.orders(user_id, flight_id, hotel_id, car_id, nights, "
                    "simulate_failure, idempotency_key) values (%(user_id)s,%(flight_id)s,%(hotel_id)s,"
                    "%(car_id)s,%(nights)s,%(simulate_failure)s,%(idempotency_key)s) "
                    "on conflict (user_id, idempotency_key) do nothing returning *", data)).fetchone()
                if row:
                    return row, True
                existing = await (await conn.execute(
                    "select * from wandersync.orders where user_id=%s and idempotency_key=%s",
                    (data["user_id"], data["idempotency_key"]))).fetchone()
                return existing, False

    async def recoverable_orders(self, pending_older_than_s: int) -> list[dict[str, Any]]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                "select * from wandersync.orders where status in ('COMPENSATING','COMPENSATION_FAILED') "
                "and updated_at < now() - interval '10 seconds' "
                "or (status='PENDING' and updated_at < now() - make_interval(secs => %s))",
                (pending_older_than_s,))
            return await cur.fetchall()

    # ---- payments (billing) -------------------------------------------------
    async def capture_payment(self, order_id: UUID, amount: Decimal, currency: str = "USD") -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "insert into wandersync.payments(order_id, amount, currency, status) values (%s,%s,%s,'CAPTURED') "
                "on conflict (order_id) do nothing", (order_id, amount, currency))

    async def refund_payment(self, order_id: UUID) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                "update wandersync.payments set status='REFUNDED', refunded_at=now() "
                "where order_id=%s and status='CAPTURED'", (order_id,))
