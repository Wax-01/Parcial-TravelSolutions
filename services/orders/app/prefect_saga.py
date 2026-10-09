"""SAGA orchestrated by Prefect: every booking is a `saga-booking` flow run (named `order-<id>`, like the UI) whose
forward steps and compensations are task runs; compensations use Prefect's retry policy. It shares the Prefect
server with the ingestion flow, so both are monitored from http://localhost:4200.

The SAGA logic stays in `SagaOrchestrator`: this class only decides *where* each step runs. If Prefect is not
reachable when a booking starts, the same saga runs without it, so Prefect is never a single point of failure.
"""
import logging
import os
import time
from typing import Any

import httpx
from prefect import allow_failure, flow, get_client, get_run_logger, task
from prefect.cache_policies import NO_CACHE
from prefect.client.schemas.filters import (FlowRunFilter, FlowRunFilterName, FlowRunFilterState,
                                            FlowRunFilterStateType)
from prefect.client.schemas.objects import StateType
from prefect.context import FlowRunContext
from prefect.runtime import task_run
from prefect.states import Completed, Crashed, Failed, State

from .saga import CANCELLED, CONFIRMED, SagaContext, SagaOrchestrator, SagaStep

log = logging.getLogger("orders.prefect")

TASK_NAMES = {  # (forward, compensation)
    "FLIGHT": ("reserve-flight", "cancel-flight"),
    "HOTEL": ("reserve-hotel", "cancel-hotel"),
    "CAR": ("reserve-car", "cancel-car"),
    "PAYMENT": ("capture-payment", "refund-payment"),
}
HEALTH_TTL = 10.0  # seconds a health check result is reused


class PrefectSagaOrchestrator(SagaOrchestrator):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._healthy_until = 0.0
        # Last task run of each running saga: the next one waits for it (`wait_for`), which is what makes Prefect
        # draw the chain reserve-flight -> reserve-hotel -> reserve-car -> cancel-hotel -> cancel-flight.
        self._last: dict[Any, State] = {}

    async def prefect_available(self) -> bool:
        api = os.environ.get("PREFECT_API_URL")
        if not api:
            return False
        if time.monotonic() < self._healthy_until:
            return True
        try:
            async with httpx.AsyncClient(timeout=1.5) as client:
                ok = (await client.get(api.rstrip("/") + "/health")).status_code == 200
        except httpx.HTTPError:
            ok = False
        if ok:
            self._healthy_until = time.monotonic() + HEALTH_TTL
        return ok

    async def run(self, order: dict[str, Any]) -> str:
        if not await self.prefect_available():
            log.warning("Prefect unreachable: order=%s runs without it", order["id"])
            return await super().run(order)

        outcome: dict[str, str] = {}

        @flow(name="saga-booking", flow_run_name=f"order-{str(order['id'])[:8]}", validate_parameters=False,
              persist_result=False)
        async def saga_booking(order_id: str, simulate_failure: str | None = None):
            outcome["started"] = "yes"
            outcome["final"] = await SagaOrchestrator.run(self, order)
            if outcome["final"] == CONFIRMED:
                return Completed(name="Confirmed", message="package booked: flight + hotel + car + payment")
            if outcome["final"] == CANCELLED:
                return Completed(name="Compensated", message="a step failed; every completed step was undone")
            return Failed(name="CompensationFailed", message="compensation pending: the reaper will retry it")

        try:
            await saga_booking(str(order["id"]), order.get("simulate_failure"), return_state=True)
        except Exception:
            log.exception("Prefect flow error for order=%s", order["id"])
        finally:
            self._last.pop(order["id"], None)
        if "final" in outcome:
            return outcome["final"]
        if "started" not in outcome:
            # The flow never started (Prefect failed while creating the run): nothing was reserved yet.
            log.warning("order=%s: Prefect flow did not start, running the saga without it", order["id"])
            return await super().run(order)
        return "UNKNOWN"  # crashed mid-flow: the reaper rolls it back

    async def mark_interrupted(self, order_id: Any) -> None:
        """The orchestrator died mid-saga (the reaper is rolling the order back): its flow run would stay
        'Running' forever in the UI, so it is closed as Crashed. Best effort."""
        if not await self.prefect_available():
            return
        try:
            async with get_client() as client:
                runs = await client.read_flow_runs(flow_run_filter=FlowRunFilter(
                    name=FlowRunFilterName(any_=[f"order-{str(order_id)[:8]}"]),
                    state=FlowRunFilterState(type=FlowRunFilterStateType(any_=[StateType.RUNNING, StateType.PENDING]))))
                for run in runs:
                    await client.set_flow_run_state(run.id, Crashed(
                        message="orders restarted mid-saga; the reaper rolled the order back"), force=True)
        except Exception:
            log.exception("could not close the Prefect flow run of order=%s", order_id)

    # ---- steps as Prefect tasks (only inside a saga-booking flow run) -------------------------------------
    async def execute_step(self, ctx: SagaContext, step: SagaStep) -> None:
        if not FlowRunContext.get():
            return await super().execute_step(ctx, step)

        async def reserve(order_id: str) -> None:  # fail fast: a failed forward step triggers compensation
            get_run_logger().info("%s for order %s", TASK_NAMES[step.name][0], order_id)
            await step.execute(ctx)

        state = await task(name=TASK_NAMES[step.name][0], cache_policy=NO_CACHE, persist_result=False)(reserve)(
            str(ctx.order_id), return_state=True, wait_for=self._after(ctx))
        self._last[ctx.order_id] = state
        if not state.is_completed():
            await state.aresult()  # re-raises the step's own exception (StepError keeps `ambiguous`)
            raise RuntimeError(f"{TASK_NAMES[step.name][0]} ended {state.name}")

    async def compensate_step(self, ctx: SagaContext, step: SagaStep) -> bool:
        if not FlowRunContext.get():
            return await super().compensate_step(ctx, step)

        async def undo(order_id: str) -> None:
            attempt = task_run.run_count  # 1-based, grows with every Prefect retry
            get_run_logger().info("%s for order %s (attempt %d)", TASK_NAMES[step.name][1], order_id, attempt)
            await self.compensate_attempt(ctx, step, attempt, reraise=True)

        delays = [self.compensation_delay(a) for a in range(1, self.compensation_attempts)]
        undo_task = task(name=TASK_NAMES[step.name][1], retries=self.compensation_attempts - 1,
                         retry_delay_seconds=delays, cache_policy=NO_CACHE, persist_result=False)(undo)
        state = await undo_task(str(ctx.order_id), return_state=True, wait_for=self._after(ctx))
        self._last[ctx.order_id] = state
        return state.is_completed()

    def _after(self, ctx: SagaContext) -> list | None:
        """Dependency on the previous task run; allow_failure so a compensation still runs after a failed step."""
        prev = self._last.get(ctx.order_id)
        return [allow_failure(prev)] if prev is not None else None
