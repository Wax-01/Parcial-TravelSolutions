"""Long-running process: registers Prefect deployments (scheduled + demo-with-failures) and runs an initial sync."""
import logging
import os
import threading
import time

import httpx
from prefect import serve

from ingestion.flows.sync import sync_travel_data

log = logging.getLogger("ingestion.serve")
logging.basicConfig(level=logging.INFO)


def wait_for_api(timeout: int = 180) -> None:
    url = os.environ["PREFECT_API_URL"].rstrip("/") + "/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if httpx.get(url, timeout=3).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2)
    raise SystemExit("Prefect API not reachable")


def initial_sync() -> None:
    try:
        sync_travel_data(mode=os.environ.get("SCRAPER_MODE", "auto"))
    except Exception:
        log.exception("initial sync failed (the scheduled deployment will retry)")


if __name__ == "__main__":
    wait_for_api()
    mode = os.environ.get("SCRAPER_MODE", "auto")
    interval = int(os.environ.get("SYNC_INTERVAL_SECONDS", "1800"))
    scheduled = sync_travel_data.to_deployment(
        name="scheduled-sync", interval=interval, parameters={"mode": mode},
        description="Periodic scraping + ingestion (Dask) into Supabase")
    demo = sync_travel_data.to_deployment(
        name="demo-with-retries", parameters={"mode": "mock", "inject_failures": 2},
        description="Each scrape fails twice before succeeding: shows Prefect retries in the UI")
    threading.Thread(target=initial_sync, daemon=True).start()
    serve(scheduled, demo)
