"""Deterministic synthetic source that mimics the shape of real travel sites (used as fallback / for flights & cars).

External ids are stable so repeated syncs UPDATE prices instead of creating duplicates; prices drift per hour.
"""
import hashlib
import random
from datetime import datetime, timedelta, timezone

from ..destinations import DESTINATIONS

SOURCE = "mock"
AIRLINES = ["Avianca", "LATAM", "Viva Air", "Wingo", "Copa", "Iberia", "American"]
HOTEL_PREFIX = ["Gran", "Plaza", "Casa", "Boutique", "Hostal", "Sol de", "Mar de", "Andes"]
HOTEL_SUFFIX = ["Central", "Real", "del Parque", "Colonial", "Palmas", "Vista", "Norte", "Centro"]
CAR_MODELS = [("Chevrolet Spark", "economy"), ("Renault Kwid", "economy"), ("Kia Picanto", "economy"),
              ("Mazda 3", "compact"), ("Toyota Corolla", "compact"), ("Renault Duster", "suv"),
              ("Toyota Prado", "suv"), ("Kia Carnival", "van")]
RENTERS = ["Localiza", "Hertz", "Avis", "Budget"]


def _rng(*parts: str) -> random.Random:
    return random.Random(int(hashlib.md5("|".join(parts).encode()).hexdigest()[:12], 16))


def _hour_bucket() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H")


def flights(origin: str, destination: str, days: int = 14) -> list[dict]:
    base = 80 + _rng("route", origin, destination).randint(0, 320)
    today = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
    out = []
    for offset in range(1, days + 1):
        day = today + timedelta(days=offset)
        for idx, hour in enumerate((6, 12, 18)):
            key = f"{origin}-{destination}-{day:%Y%m%d}-{idx}"
            static, drift = _rng("flight", key), _rng("price", key, _hour_bucket())
            dep = day + timedelta(hours=hour, minutes=static.choice([0, 15, 30, 45]))
            dur = timedelta(minutes=45 + (base // 2))
            out.append({
                "source": SOURCE, "external_id": key, "airline": static.choice(AIRLINES),
                "origin": origin, "destination": destination, "departure_at": dep, "arrival_at": dep + dur,
                "price": round(base * (0.8 + drift.random() * 0.8), 2), "currency": "USD",
                "seats_available": static.randint(20, 150)})
    return out


def hotels(city: str) -> list[dict]:
    out = []
    for i in range(8):
        key = f"{city}-{i}"
        static, drift = _rng("hotel", key), _rng("hprice", key, _hour_bucket())
        stars = static.randint(2, 5)
        out.append({
            "source": SOURCE, "external_id": key,
            "name": f"{static.choice(HOTEL_PREFIX)} {DESTINATIONS.get(city, city)} {static.choice(HOTEL_SUFFIX)}",
            "city": city, "stars": stars,
            "price_per_night": round((25 + stars * 22) * (0.85 + drift.random() * 0.5), 2), "currency": "USD",
            "rooms_available": static.randint(3, 30)})
    return out


def cars(city: str) -> list[dict]:
    out = []
    for i, (model, category) in enumerate(CAR_MODELS):
        key = f"{city}-{i}"
        static, drift = _rng("car", key), _rng("cprice", key, _hour_bucket())
        base = {"economy": 22, "compact": 30, "suv": 55, "van": 70}[category]
        out.append({
            "source": SOURCE, "external_id": key, "provider": static.choice(RENTERS), "model": model,
            "category": category, "city": city,
            "price_per_day": round(base * (0.85 + drift.random() * 0.4), 2), "currency": "USD",
            "units_available": static.randint(2, 12)})
    return out
