"""Orders/Billing service: SAGA orchestrator for the flight + hotel + car package."""
import asyncio
import logging
from contextlib import asynccontextmanager
from decimal import Decimal
from typing import Any, Literal
from uuid import UUID

import httpx
import psycopg.errors
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from wandersync_common.config import env, env_bool, internal_token
from wandersync_common.db import make_pool
from wandersync_common.internal import require_internal

from .saga import (CANCELLED, COMPENSATING, COMPENSATION_FAILED, SagaContext, SagaOrchestrator, SagaStep,
                   StepError)
from .store import PgStore

logging.basicConfig(level=logging.INFO, format="%(asctime)s orders %(levelname)s %(message)s")
log = logging.getLogger("orders")

pool = make_pool(max_size=3)
store = PgStore(pool)
http: httpx.AsyncClient
running: set[UUID] = set()
tasks: set[asyncio.Task] = set()
sem = asyncio.Semaphore(20)

SimulatedFailure = Literal["FLIGHT", "HOTEL", "CAR", "PAYMENT", "CAR_TIMEOUT", "CAR_FLAKY_COMPENSATION"]


def _sim(ctx: SagaContext) -> str | None:
    return ctx.order.get("simulate_failure") if env_bool("ALLOW_FAILURE_SIMULATION", True) else None


def http_step(name: str, base_url: str, fail_on: set[str], payload) -> SagaStep:
    """A participant reached over HTTP: reserve = POST /reservations, compensate = DELETE /reservations/{order}."""
    headers = {"X-Internal-Token": internal_token()}

    async def execute(ctx: SagaContext) -> None:
        sim = _sim(ctx)
        h = dict(headers)
        if sim in fail_on:
            h["X-Simulate-Failure"] = "timeout" if sim == "CAR_TIMEOUT" else "error"
        try:
            resp = await http.post(f"{base_url}/reservations", json=payload(ctx), headers=h)
        except httpx.HTTPError as exc:  # timeout / connection lost: outcome unknown
            raise StepError(f"{name} service unreachable: {type(exc).__name__}", ambiguous=True) from exc
        if resp.status_code >= 400:
            ambiguous = resp.status_code in (500, 502, 504)
            raise StepError(f"{name} service replied {resp.status_code}: {resp.text[:120]}", ambiguous=ambiguous)
        ctx.amounts[name] = Decimal(resp.json()["amount"])

    async def compensate(ctx: SagaContext) -> None:
        h = dict(headers)
        sim = _sim(ctx)
        if sim == "CAR_FLAKY_COMPENSATION" and name == "HOTEL":
            h["X-Simulate-Failure"] = "flaky:2"  # first two cancel calls fail -> demonstrates retries
        resp = await http.delete(f"{base_url}/reservations/{ctx.order_id}", headers=h)
        resp.raise_for_status()

    return SagaStep(name, execute, compensate)


def build_steps() -> list[SagaStep]:
    async def pay(ctx: SagaContext) -> None:
        if _sim(ctx) == "PAYMENT":
            raise StepError("payment declined (simulated)")
        await store.capture_payment(ctx.order_id, ctx.total)

    async def refund(ctx: SagaContext) -> None:
        await store.refund_payment(ctx.order_id)

    return [
        http_step("FLIGHT", env("FLIGHTS_URL"), {"FLIGHT"},
                  lambda c: {"order_id": str(c.order_id), "flight_id": str(c.order["flight_id"])}),
        http_step("HOTEL", env("HOTELS_URL"), {"HOTEL"},
                  lambda c: {"order_id": str(c.order_id), "hotel_id": str(c.order["hotel_id"]),
                             "nights": c.order["nights"]}),
        http_step("CAR", env("CARS_URL"), {"CAR", "CAR_TIMEOUT", "CAR_FLAKY_COMPENSATION"},
                  lambda c: {"order_id": str(c.order_id), "car_id": str(c.order["car_id"]),
                             "days": c.order["nights"]}),
        SagaStep("PAYMENT", pay, refund),
    ]


orchestrator: SagaOrchestrator


async def run_saga(order: dict[str, Any]) -> None:
    async with sem:
        running.add(order["id"])
        try:
            await orchestrator.run(order)
        finally:
            running.discard(order["id"])


def spawn(order: dict[str, Any]) -> None:
    task = asyncio.create_task(run_saga(order))
    tasks.add(task)
    task.add_done_callback(tasks.discard)


async def recover_once() -> None:
    """Resume interrupted sagas: anything not finished is rolled back (safe abort), never left half-done."""
    for order in await store.recoverable_orders(pending_older_than_s=120):
        if order["id"] in running:
            continue
        log.warning("recovering order=%s status=%s", order["id"], order["status"])
        running.add(order["id"])
        try:
            ctx = SagaContext(order_id=order["id"], order=order)
            await orchestrator.compensate(ctx, list(reversed(orchestrator.steps)),
                                          reason=order.get("failure_reason") or "recovered after interruption")
        finally:
            running.discard(order["id"])


async def reaper_loop() -> None:
    while True:
        try:
            await recover_once()
        except Exception:
            log.exception("reaper iteration failed")
        await asyncio.sleep(15)


@asynccontextmanager
async def lifespan(_: FastAPI):
    global http, orchestrator
    await pool.open()
    http = httpx.AsyncClient(timeout=httpx.Timeout(4.0, connect=2.0))
    orchestrator = SagaOrchestrator(
        store, build_steps(), step_delay=int(env("SAGA_STEP_DELAY_MS", "700")) / 1000,
        compensation_attempts=5, backoff=0.5)
    reaper = asyncio.create_task(reaper_loop())
    yield
    reaper.cancel()
    await http.aclose()
    await pool.close()


app = FastAPI(title="WanderSync Orders", lifespan=lifespan)


class CreateOrder(BaseModel):
    user_id: UUID
    flight_id: UUID
    hotel_id: UUID
    car_id: UUID
    nights: int = Field(ge=1, le=60)
    idempotency_key: str | None = Field(default=None, max_length=100)
    simulate_failure: SimulatedFailure | None = None


@app.get("/healthz")
async def healthz():
    async with pool.connection() as conn:
        await conn.execute("select 1")
    return {"status": "ok"}


@app.post("/orders", status_code=202, dependencies=[Depends(require_internal)])
async def create_order(body: CreateOrder):
    if body.simulate_failure and not env_bool("ALLOW_FAILURE_SIMULATION", True):
        raise HTTPException(403, "failure simulation disabled")
    try:
        order, created = await store.create_order(body.model_dump(mode="python"))
    except psycopg.errors.ForeignKeyViolation:
        raise HTTPException(422, "unknown flight, hotel, car or user")
    if created:
        spawn(order)
    return {"id": str(order["id"]), "status": order["status"], "idempotent": not created}
