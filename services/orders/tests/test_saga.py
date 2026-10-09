import asyncio
from decimal import Decimal
from uuid import uuid4

import pytest

from app.saga import (CANCELLED, COMPENSATION_FAILED, CONFIRMED, SagaOrchestrator, SagaStep, StepError)


class MemoryStore:
    def __init__(self):
        self.events, self.status, self.fields = [], "PENDING", {}

    async def log(self, order_id, step, action, status, detail=None):
        self.events.append((step, action, status))

    async def set_status(self, order_id, status, **fields):
        self.status = status
        self.fields.update(fields)


def make_steps(fail_at=None, ambiguous=False, flaky_compensation: dict[str, int] | None = None):
    """Four steps that record effects in `state`; `fail_at` makes that step's execute fail."""
    state = {"reserved": [], "cancel_calls": []}
    flaky = dict(flaky_compensation or {})
    prices = {"FLIGHT": Decimal("100"), "HOTEL": Decimal("200"), "CAR": Decimal("50"), "PAYMENT": Decimal("0")}

    def build(name):
        async def execute(ctx):
            if name == fail_at:
                raise StepError(f"{name} boom", ambiguous=ambiguous)
            state["reserved"].append(name)
            ctx.amounts[name] = prices[name]

        async def compensate(ctx):
            state["cancel_calls"].append(name)
            if flaky.get(name, 0) > 0:
                flaky[name] -= 1
                raise RuntimeError("transient")
            if name in state["reserved"]:
                state["reserved"].remove(name)

        return SagaStep(name, execute, compensate)

    return [build(n) for n in ("FLIGHT", "HOTEL", "CAR", "PAYMENT")], state


def run(steps, store=None, attempts=3):
    store = store or MemoryStore()
    orch = SagaOrchestrator(store, steps, compensation_attempts=attempts, backoff=0)
    final = asyncio.run(orch.run({"id": uuid4()}))
    return final, store


def test_happy_path_confirms_and_sums_total():
    steps, state = make_steps()
    final, store = run(steps)
    assert final == CONFIRMED and store.status == CONFIRMED
    assert store.fields["total_amount"] == Decimal("350")
    assert state["reserved"] == ["FLIGHT", "HOTEL", "CAR", "PAYMENT"]
    assert state["cancel_calls"] == []


def test_car_failure_cancels_hotel_then_flight_in_reverse_order():
    steps, state = make_steps(fail_at="CAR")
    final, store = run(steps)
    assert final == CANCELLED
    assert state["cancel_calls"] == ["HOTEL", "FLIGHT"]  # reverse order, failed step not compensated
    assert state["reserved"] == []  # no orphan reservations
    assert ("CAR", "EXECUTE", "FAILED") in store.events


def test_payment_failure_cancels_everything():
    steps, state = make_steps(fail_at="PAYMENT")
    final, _ = run(steps)
    assert final == CANCELLED
    assert state["cancel_calls"] == ["CAR", "HOTEL", "FLIGHT"]
    assert state["reserved"] == []


def test_first_step_failure_has_nothing_to_compensate():
    steps, state = make_steps(fail_at="FLIGHT")
    final, _ = run(steps)
    assert final == CANCELLED and state["cancel_calls"] == []


def test_ambiguous_failure_also_compensates_the_failed_step():
    steps, state = make_steps(fail_at="CAR", ambiguous=True)  # e.g. timeout: car may have been booked
    final, _ = run(steps)
    assert final == CANCELLED
    assert state["cancel_calls"] == ["CAR", "HOTEL", "FLIGHT"]


def test_compensation_is_retried_until_it_succeeds():
    steps, state = make_steps(fail_at="CAR", flaky_compensation={"HOTEL": 2})
    final, store = run(steps, attempts=5)
    assert final == CANCELLED
    assert state["cancel_calls"].count("HOTEL") == 3  # 2 failures + 1 success
    assert state["reserved"] == []
    assert store.events.count(("HOTEL", "COMPENSATE", "FAILED")) == 2


def test_exhausted_compensation_is_flagged_not_hidden():
    steps, state = make_steps(fail_at="CAR", flaky_compensation={"HOTEL": 99})
    final, store = run(steps, attempts=3)
    assert final == COMPENSATION_FAILED and store.status == COMPENSATION_FAILED
    assert state["reserved"] == ["HOTEL"]  # flight still compensated; hotel left for the reaper
    assert "FLIGHT" not in state["reserved"]


def test_unexpected_exception_still_compensates():
    steps, state = make_steps()

    async def crash(ctx):
        raise ValueError("bug")

    steps[2] = SagaStep("CAR", crash, steps[2].compensate)
    final, _ = run(steps)
    assert final == CANCELLED and state["reserved"] == []


def test_prefect_orchestrator_falls_back_to_plain_saga_when_prefect_is_down(monkeypatch):
    pytest.importorskip("prefect")
    from app.prefect_saga import PrefectSagaOrchestrator

    monkeypatch.setenv("PREFECT_API_URL", "http://127.0.0.1:9/api")  # nothing listens there
    steps, state = make_steps(fail_at="CAR", flaky_compensation={"HOTEL": 2})
    store = MemoryStore()
    orch = PrefectSagaOrchestrator(store, steps, compensation_attempts=5, backoff=0)
    assert asyncio.run(orch.run({"id": uuid4()})) == CANCELLED
    assert state["cancel_calls"] == ["HOTEL", "HOTEL", "HOTEL", "FLIGHT"] and state["reserved"] == []
