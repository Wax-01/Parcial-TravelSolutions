"""Real source: KAYAK search results for flights and rental cars, rendered with Scrapling.

KAYAK's class names are obfuscated and change often, so results are read from their visible text and accessible
attributes (result containers, "Vehicle type: ..." / "Car agency: ..." image alt texts), not from styling classes.
"""
import hashlib
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from selectolax.parser import HTMLParser

from ..destinations import AIRPORT_TZ
from . import ScrapeBlockedError, render

SOURCE = "kayak"
FLIGHT_RESULT = '[aria-label^="Result item"]'
CAR_RESULT = ".js-result"
_TIME = re.compile(r"^\d{1,2}:\d{2} ?[ap]m$", re.I)
_PRICE = re.compile(r"^\$([\d,]+)$")
_PLUS_DAYS = re.compile(r"^\+(\d)$")
_VEHICLE = re.compile(r"Vehicle type: (?P<category>[^-]+?) - (?P<model>.+?)(?: or similar)?$")


def _tokens(node) -> list[str]:
    return [t.strip() for t in node.text(separator="\n").split("\n") if t.strip()]


def _hash(*parts) -> str:
    return hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:16]


def parse_flights(html: str, origin: str, destination: str, day: date) -> list[dict]:
    out = []
    for item in HTMLParser(html).css(FLIGHT_RESULT):
        tok = _tokens(item)
        if "Ad" in tok:  # sponsored cards link to other sites, not to a bookable fare
            continue
        times = [i for i, t in enumerate(tok) if _TIME.match(t)]
        price = next((_PRICE.match(t) for t in tok if _PRICE.match(t)), None)
        if len(times) < 2 or not price:
            continue
        after = tok[times[1] + 1:]
        plus = int(_PLUS_DAYS.match(after[0])[1]) if after and _PLUS_DAYS.match(after[0]) else 0
        airline = after[1] if plus else (after[0] if after else "")
        if not airline:
            continue
        clock = lambda t: datetime.strptime(t.replace(" ", "").upper(), "%I:%M%p").time()  # noqa: E731
        dep = datetime.combine(day, clock(tok[times[0]]), ZoneInfo(AIRPORT_TZ[origin]))
        arr = datetime.combine(day + timedelta(days=plus), clock(tok[times[1]]), ZoneInfo(AIRPORT_TZ[destination]))
        if arr <= dep:
            arr += timedelta(days=1)
        out.append({
            "source": SOURCE, "external_id": _hash(origin, destination, dep.isoformat(), airline),
            "airline": airline, "origin": origin, "destination": destination, "departure_at": dep,
            "arrival_at": arr, "price": float(price[1].replace(",", "")), "currency": "USD", "seats_available": 9})
    if not out:
        raise ScrapeBlockedError("no parseable flights (bot wall or changed markup)")
    return out


def parse_cars(html: str, city: str, days: int) -> list[dict]:
    out = []
    for item in HTMLParser(html).css(CAR_RESULT):
        alts = [n.attributes.get("alt") or "" for n in item.css("img[alt]")]
        vehicle = next((_VEHICLE.match(a) for a in alts if _VEHICLE.match(a)), None)
        agency = next((a.split(":", 1)[1].strip() for a in alts if a.startswith("Car agency:")), None)
        price = next((_PRICE.match(t) for t in _tokens(item) if _PRICE.match(t)), None)
        if not (vehicle and agency and price):
            continue
        model = vehicle["model"].removeprefix("Class ").strip()
        category = vehicle["category"].strip().lower()
        out.append({
            "source": SOURCE, "external_id": _hash(city, agency, category, model), "provider": agency,
            "model": model, "category": category, "city": city,
            "price_per_day": round(float(price[1].replace(",", "")) / days, 2), "currency": "USD",
            "units_available": 5})
    if not out:
        raise ScrapeBlockedError("no parseable cars (bot wall or changed markup)")
    return out


def flights(origin: str, destination: str, day: date) -> list[dict]:
    url = f"https://www.kayak.com/flights/{origin}-{destination}/{day.isoformat()}?sort=bestflight_a&currency=USD"
    return parse_flights(render(url, FLIGHT_RESULT).html_content, origin, destination, day)


def cars(city: str, days: int = 3) -> list[dict]:
    pickup = date.today() + timedelta(days=7)
    url = f"https://www.kayak.com/cars/{city}/{pickup}/{pickup + timedelta(days=days)}?sort=rank_a&currency=USD"
    return parse_cars(render(url, CAR_RESULT).html_content, city, days)
