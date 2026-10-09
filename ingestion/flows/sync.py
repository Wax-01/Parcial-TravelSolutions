"""Prefect flow that orchestrates distributed scraping/ingestion on the Dask cluster.

scrape (per partition, parallel on Dask workers) -> clean -> upsert (batched, to Supabase)
Every stage has explicit retry policies; progress is visible in the Prefect UI, execution in the Dask dashboard.
"""
import os
from datetime import date, timedelta
from typing import Any

import httpx
from prefect import flow, get_run_logger, task
from prefect.runtime import flow_run, task_run
from prefect_dask import DaskTaskRunner

# Absolute imports: Prefect deployments load this file as a script (relative imports would fail).
from ingestion import pipeline
from ingestion.destinations import DESTINATIONS, ROUTES
from ingestion.scrapers import ScrapeError, TransientScrapeError, booking, google_flights, kayak, mock

SCRAPE_RETRIES = 3
RETRY_DELAYS = [5, 15, 45]  # seconds, exponential-ish backoff
# Departure days (offsets from today) scraped for every route from the real flight sources.
FLIGHT_DAY_OFFSETS = [int(d) for d in os.environ.get("SCRAPE_FLIGHT_DAYS", "3,7").split(",") if d.strip()]


def scrape_real(kind: str, key: str) -> tuple[list[dict[str, Any]], str]:
    """Real sources: Booking.com (hotels), Google Flights + KAYAK (flights), KAYAK (cars)."""
    if kind == "hotels":
        return booking.hotels(key), "booking.com"
    if kind == "cars":
        return kayak.cars(key), "kayak"
    origin, destination = key.split("-")
    records, used, errors = [], [], []
    for offset in FLIGHT_DAY_OFFSETS:
        day = date.today() + timedelta(days=offset)
        for source in (google_flights, kayak):
            try:
                records += source.flights(origin, destination, day)
                used.append(source.SOURCE)
            except ScrapeError as exc:  # one source down is fine while the other one answers
                errors.append(exc)
    if not records:
        raise errors[-1]
    return records, "+".join(sorted(set(used)))


# No task-level timeout: on Dask worker threads Prefect cannot interrupt it; each page load has its own 45 s
# timeout in scrapers.render() and a timeout there surfaces as a retryable error.
@task(name="scrape-partition", retries=SCRAPE_RETRIES, retry_delay_seconds=RETRY_DELAYS,
      retry_jitter_factor=0.3)
def scrape(kind: str, key: str, mode: str = "auto", inject_failures: int = 0) -> list[dict[str, Any]]:
    """One unit of work: a route (flights) or a city (hotels / cars). Runs on a Dask worker."""
    log = get_run_logger()
    attempt = task_run.run_count  # 1-based; increases on every Prefect retry
    last_attempt = attempt > SCRAPE_RETRIES

    if attempt <= inject_failures:  # demo hook: prove retries in the Prefect UI
        raise TransientScrapeError(f"injected network failure (attempt {attempt}/{inject_failures})")

    try:
        if mode in ("auto", "real"):
            records, source = scrape_real(kind, key)
            log.info("scraped %d %s for %s from %s", len(records), kind, key, source)
            return records
    except (ScrapeError, httpx.HTTPError) as exc:
        if mode == "auto" and last_attempt:
            log.warning("real source failed after %d attempts (%s) -> falling back to mock data", attempt, exc)
        else:
            raise

    if kind == "flights":
        origin, destination = key.split("-")
        records = mock.flights(origin, destination)
    elif kind == "hotels":
        records = mock.hotels(key)
    else:
        records = mock.cars(key)
    log.info("generated %d %s records for %s (mock source)", len(records), kind, key)
    return records


@task(name="clean-normalize")
def clean(kind: str, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cleaned = pipeline.clean(kind, records)
    get_run_logger().info("%s: %d raw -> %d clean", kind, len(records), len(cleaned))
    return cleaned


@task(name="upsert-supabase", retries=3, retry_delay_seconds=[2, 5, 10], retry_jitter_factor=0.2)
def upsert(kind: str, records: list[dict[str, Any]]) -> int:
    n = pipeline.upsert(kind, records)
    get_run_logger().info("%s: upserted %d rows", kind, n)
    return n


@flow(name="sync-travel-data", log_prints=True,
      task_runner=DaskTaskRunner(address=os.environ.get("DASK_SCHEDULER", "tcp://dask-scheduler:8786")))
def sync_travel_data(mode: str = "auto", inject_failures: int = 0) -> dict[str, int]:
    """mode: auto (real, fallback to mock) | real | mock. inject_failures: fail first N attempts of every scrape."""
    partitions = ([("flights", f"{o}-{d}") for o, d in ROUTES]
                  + [("hotels", c) for c in DESTINATIONS] + [("cars", c) for c in DESTINATIONS])
    chains = []
    for kind, key in partitions:
        scraped = scrape.submit(kind, key, mode, inject_failures)
        cleaned = clean.submit(kind, scraped)
        chains.append((kind, upsert.submit(kind, cleaned)))

    summary: dict[str, int] = {"flights": 0, "hotels": 0, "cars": 0}
    failed = 0
    for kind, fut in chains:
        rows = fut.result(raise_on_failure=False)
        if isinstance(rows, int):
            summary[kind] += rows
        else:
            failed += 1
    status = "COMPLETED" if not failed else "PARTIAL"
    pipeline.record_run(flow_run.id or "local", summary, status)
    print(f"sync finished: {summary}, failed partitions: {failed}")
    if failed:
        raise RuntimeError(f"{failed} partitions failed after retries")
    return summary
