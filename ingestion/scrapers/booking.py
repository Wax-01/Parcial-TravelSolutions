"""Real source: Booking.com public search results (hotels). Best effort, low volume (1 request per city).

Booking uses bot protection, so any block / unexpected markup raises ScrapeBlockedError and the Prefect
task retries with backoff; in SCRAPER_MODE=auto the flow falls back to the mock source after the last retry.
"""
import hashlib
import re
from datetime import date, timedelta
from urllib.parse import quote_plus

import httpx
from selectolax.parser import HTMLParser

from ..destinations import DESTINATIONS
from . import ScrapeBlockedError, TransientScrapeError

SOURCE = "booking.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}
_PRICE = re.compile(r"([\d][\d.,\s]*)")


def _parse_price(text: str) -> float | None:
    m = _PRICE.search(text.replace("\xa0", " "))
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(1))
    return float(digits) if digits else None


def parse_results(html: str, city: str, nights: int) -> list[dict]:
    tree = HTMLParser(html)
    cards = tree.css('[data-testid="property-card"]')
    if not cards:
        raise ScrapeBlockedError("no property cards (bot wall or changed markup)")
    out = []
    for card in cards:
        title = card.css_first('[data-testid="title"]')
        price = card.css_first('[data-testid="price-and-discounted-price"]')
        link = card.css_first('a[data-testid="title-link"]')
        if not (title and price):
            continue
        amount = _parse_price(price.text())
        if not amount:
            continue
        stars = len(card.css('[data-testid="rating-stars"] span')) or None
        href = link.attributes.get("href", "") if link else title.text()
        out.append({
            "source": SOURCE, "external_id": hashlib.md5(href.split("?")[0].encode()).hexdigest()[:16],
            "name": title.text().strip(), "city": city, "stars": stars,
            "price_per_night": round(amount / max(nights, 1), 2), "currency": "USD",
            "rooms_available": 5})
    if not out:
        raise ScrapeBlockedError("cards found but none parseable")
    return out


def hotels(city: str, nights: int = 2, timeout: float = 20.0) -> list[dict]:
    checkin = date.today() + timedelta(days=30)
    url = ("https://www.booking.com/searchresults.html?ss=" + quote_plus(DESTINATIONS.get(city, city))
           + f"&checkin={checkin}&checkout={checkin + timedelta(days=nights)}"
           "&group_adults=2&no_rooms=1&selected_currency=USD&lang=en-us")
    try:
        resp = httpx.get(url, headers=HEADERS, timeout=timeout, follow_redirects=True)
    except httpx.HTTPError as exc:
        raise TransientScrapeError(f"network error: {type(exc).__name__}") from exc
    if resp.status_code in (202, 403, 429) or "captcha" in resp.text[:5000].lower():
        raise ScrapeBlockedError(f"blocked by source (HTTP {resp.status_code})")
    if resp.status_code >= 500:
        raise TransientScrapeError(f"source error HTTP {resp.status_code}")
    return parse_results(resp.text, city, nights)
