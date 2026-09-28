"""SAGA orchestrator (pure logic, no I/O of its own).

Forward path:  FLIGHT -> HOTEL -> CAR -> PAYMENT -> CONFIRMED
On failure at step N: compensate the completed steps in reverse order (and step N itself when
its outcome is ambiguous, e.g. timeout), retrying each compensation with exponential backoff.
Compensations are idempotent, so retrying (also from the background reaper) is always safe.
"""
import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Awaitable, Callable, Protocol
from uuid import UUID

log = logging.getLogger("saga")

# Terminal / intermediate order states
PENDING, CONFIRMED = "PENDING", "CONFIRMED"
COMPENSATING, CANCELLED, COMPENSATION_FAILED = "COMPENSATING", "CANCELLED", "COMPENSATION_FAILED"


class StepError(Exception):
    """A forward step failed. `ambiguous` means we cannot tell whether it took effect (timeout, 5xx)."""

    def __init__(self, message: str, ambiguous: bool = False):
        super().__init__(message)
        self.ambiguous = ambiguous


@dataclass
class SagaContext:
    order_id: UUID
    order: dict[str, Any]
    amounts: dict[str, Decimal] = field(default_factory=dict)

    @property
    def total(self) -> Decimal:
        return sum(self.amounts.values(), Decimal("0"))


@dataclass
class SagaStep:
    name: str
    execute: Callable[[SagaContext], Awaitable[None]]
    compensate: Callable[[SagaContext], Awaitable[None]]


class SagaStore(Protocol):
    async def log(self, order_id: UUID, step: str, action: str, status: str, detail: str | None = None) -> None: ...
    async def set_status(self, order_id: UUID, status: str, **fields: Any) -> None: ...


class SagaOrchestrator:
    def __init__(self, store: SagaStore, steps: list[SagaStep], step_delay: float = 0.0,
                 compensation_attempts: int = 5, backoff: float = 0.5):
        self.store = store
        self.steps = steps
        self.step_delay = step_delay
        self.compensation_attempts = compensation_attempts
        self.backoff = backoff

    async def run(self, order: dict[str, Any]) -> str:
        ctx = SagaContext(order_id=order["id"], order=order)
        completed: list[SagaStep] = []
        for step in self.steps:
            await self.store.log(ctx.order_id, step.name, "EXECUTE", "STARTED")
            try:
                await step.execute(ctx)
            except StepError as exc:
                await self.store.log(ctx.order_id, step.name, "EXECUTE", "FAILED", str(exc))
                log.warning("order=%s step=%s failed: %s -> compensating", ctx.order_id, step.name, exc)
                to_undo = completed + ([step] if exc.ambiguous else [])
                return await self.compensate(ctx, list(reversed(to_undo)), reason=f"{step.name}: {exc}")
            except Exception as exc:  # unexpected bug in a step: still never leave an orphan
                await self.store.log(ctx.order_id, step.name, "EXECUTE", "FAILED", f"unexpected: {exc!r}")
                log.exception("order=%s step=%s crashed", ctx.order_id, step.name)
                return await self.compensate(ctx, list(reversed(completed + [step])),
                                             reason=f"{step.name}: unexpected error")
            await self.store.log(ctx.order_id, step.name, "EXECUTE", "SUCCEEDED")
            completed.append(step)
            if self.step_delay:
                await asyncio.sleep(self.step_delay)
        await self.store.set_status(ctx.order_id, CONFIRMED, total_amount=ctx.total)
        log.info("order=%s CONFIRMED total=%s", ctx.order_id, ctx.total)
        return CONFIRMED

    async def compensate(self, ctx: SagaContext, steps: list[SagaStep], reason: str) -> str:
        """Undo `steps` (already in reverse order). Retries each with backoff; never raises."""
        await self.store.set_status(ctx.order_id, COMPENSATING, failure_reason=reason[:500])
        all_ok = True
        for step in steps:
            ok = False
            for attempt in range(1, self.compensation_attempts + 1):
                await self.store.log(ctx.order_id, step.name, "COMPENSATE", "STARTED", f"attempt {attempt}")
                try:
                    await step.compensate(ctx)
                    await self.store.log(ctx.order_id, step.name, "COMPENSATE", "SUCCEEDED")
                    ok = True
                    break
                except Exception as exc:
                    await self.store.log(ctx.order_id, step.name, "COMPENSATE", "FAILED",
                                         f"attempt {attempt}: {exc}")
                    if attempt < self.compensation_attempts:
                        await asyncio.sleep(self.backoff * (2 ** (attempt - 1)))
            all_ok = all_ok and ok
        final = CANCELLED if all_ok else COMPENSATION_FAILED
        await self.store.set_status(ctx.order_id, final)
        log.info("order=%s %s", ctx.order_id, final)
        return final
