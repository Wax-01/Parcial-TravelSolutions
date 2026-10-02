"""Real source: Google Flights one-way search results (flights), rendered with Scrapling.

Each result exposes its whole itinerary in an accessible label, which is far more stable than the obfuscated classes:
"From 26 US dollars. Nonstop flight with LATAM. ... Leaves El Dorado International Airport at 5:35 PM on Sunday,
November 1 and arrives at Jose Maria Cordova International Airport at 6:40 PM on Sunday, November 1. ..."
"""
import hashlib
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

from selectolax.parser import HTMLParser

from ..destinations import AIRPORT_TZ
from . import ScrapeBlockedError, render

SOURCE = "google_flights"
RESULT = '[aria-label^="From "]'
_ITINERARY = re.compile(
    r"From ([\d,]+) US dollars\. (?:Nonstop|\d+ stops?) flight with (?P<airline>[^.]+)\..*?"
    r"Leaves .+? at (?P<dt>\d{1,2}:\d{2} [AP]M) on \w+, (?P<dd>\w+ \d{1,2}) "
    r"and arrives at .+? at (?P<at>\d{1,2}:\d{2} [AP]M) on \w+, (?P<ad>\w+ \d{1,2})", re.S)


def _local(day_text: str, time_text: str, year: int, tz: str) -> datetime:
    naive = datetime.strptime(f"{day_text} {year} {time_text}", "%B %d %Y %I:%M %p")
    return naive.replace(tzinfo=ZoneInfo(tz))


def parse_results(html: str, origin: str, destination: str, day: date) -> list[dict]:
    out = []
    for node in HTMLParser(html).css(RESULT):
        label = " ".join((node.attributes.get("aria-label") or "").split())  # also folds \u202f / \xa0 / newlines
        m = _ITINERARY.search(label)
        if not m:
            continue
        dep = _local(m["dd"], m["dt"], day.year, AIRPORT_TZ[origin])
        arr = _local(m["ad"], m["at"], day.year, AIRPORT_TZ[destination])
        if arr.month < dep.month:  # crosses New Year
            arr = arr.replace(year=arr.year + 1)
        airline = m["airline"].strip()
        out.append({
            "source": SOURCE,
            "external_id": hashlib.md5(f"{origin}{destination}{dep.isoformat()}{airline}".encode()).hexdigest()[:16],
            "airline": airline, "origin": origin, "destination": destination,
            "departure_at": dep, "arrival_at": arr, "price": float(m.group(1).replace(",", "")),
            "currency": "USD", "seats_available": 9})
    if not out:
        raise ScrapeBlockedError("no parseable flights (bot wall or changed markup)")
    return out


def flights(origin: str, destination: str, day: date) -> list[dict]:
    url = (f"https://www.google.com/travel/flights?q=Flights%20to%20{destination}%20from%20{origin}"
           f"%20on%20{day.isoformat()}%20oneway&curr=USD&hl=en&gl=us")
    return parse_results(render(url, RESULT).html_content, origin, destination, day)
