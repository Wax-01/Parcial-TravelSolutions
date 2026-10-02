"""Real source: Booking.com public search results (hotels). Best effort, low volume (1 request per city).

Booking answers plain HTTP clients with an AWS WAF JavaScript challenge (HTTP 202), so the page is rendered with
Scrapling's DynamicFetcher (headless Chromium via Playwright), which solves it like a real browser. Any block /
unexpected markup still raises ScrapeBlockedError and the Prefect task retries with backoff; in SCRAPER_MODE=auto
the flow falls back to the mock source after the last retry.
"""
import hashlib
import re
from datetime import date, timedelta
from urllib.parse import quote_plus

from selectolax.parser import HTMLParser

from ..destinations import DESTINATIONS
from . import ScrapeBlockedError, render

SOURCE = "booking.com"
CARD = '[data-testid="property-card"]'
_PRICE = re.compile(r"([\d][\d.,\s]*)")
_RATING = re.compile(r"rating:\s*([1-5])\s*out of 5", re.I)


def _parse_price(text: str) -> float | None:
    m = _PRICE.search(text.replace("\xa0", " "))
    if not m:
        return None
    digits = re.sub(r"[^\d]", "", m.group(1))
    return float(digits) if digits else None


def _stars(card) -> int | None:
    """Official rating: the aria-label ("Property rating: 4 out of 5 ...") or the icon count (one child per icon)."""
    for node in card.css("[aria-label]"):
        m = _RATING.search(node.attributes.get("aria-label") or "")
        if m:
            return int(m.group(1))
    icons = card.css_first('[data-testid="rating-stars"], [data-testid="rating-squares"]')
    n = len([c for c in icons.iter() if c.tag != "-text"]) if icons else 0
    return n if 1 <= n <= 5 else None


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
        stars = _stars(card)
        href = link.attributes.get("href", "") if link else title.text()
        out.append({
            "source": SOURCE, "external_id": hashlib.md5(href.split("?")[0].encode()).hexdigest()[:16],
            "name": title.text().strip(), "city": city, "stars": stars,
            "price_per_night": round(amount / max(nights, 1), 2), "currency": "USD",
            "rooms_available": 5})
    if not out:
        raise ScrapeBlockedError("cards found but none parseable")
    return out


def hotels(city: str, nights: int = 2) -> list[dict]:
    checkin = date.today() + timedelta(days=30)
    url = ("https://www.booking.com/searchresults.html?ss=" + quote_plus(DESTINATIONS.get(city, city))
           + f"&checkin={checkin}&checkout={checkin + timedelta(days=nights)}"
           "&group_adults=2&no_rooms=1&selected_currency=USD&lang=en-us")
    return parse_results(render(url, CARD).html_content, city, nights)
