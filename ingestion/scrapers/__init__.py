"""Scrapers: real sources (Booking.com, Google Flights, Kayak) rendered with Scrapling, plus a synthetic fallback."""
import threading

# One headless browser at a time per worker process (keeps each Dask worker inside its memory limit).
_BROWSER = threading.Semaphore(1)


class ScrapeError(Exception):
    """Base class for scraping failures (retried by Prefect)."""


class ScrapeBlockedError(ScrapeError):
    """The source answered with a bot-wall / captcha / unexpected markup."""


class TransientScrapeError(ScrapeError):
    """Network-level or injected transient failure."""


def render(url: str, wait_selector: str, timeout: float = 45.0):
    """Render a page with Scrapling's DynamicFetcher (headless Chromium): solves the JS anti-bot challenges that
    plain HTTP clients get (HTTP 202 / bot walls). Returns a Scrapling Response, or raises a ScrapeError."""
    from scrapling.fetchers import DynamicFetcher  # heavy import (Playwright): only where a real source is used

    try:
        with _BROWSER:
            # disable_resources stays off: blocking resources breaks the anti-bot challenges.
            page = DynamicFetcher.fetch(url, headless=True, network_idle=True, wait_selector=wait_selector,
                                        timeout=int(timeout * 1000))
    except Exception as exc:  # Playwright timeouts / navigation errors
        raise TransientScrapeError(f"browser error: {type(exc).__name__}") from exc
    if page.status in (202, 403, 429):
        raise ScrapeBlockedError(f"blocked by source (HTTP {page.status})")
    if page.status >= 500:
        raise TransientScrapeError(f"source error HTTP {page.status}")
    return page
