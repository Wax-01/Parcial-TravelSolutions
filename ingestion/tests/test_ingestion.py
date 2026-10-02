import os
from datetime import date, datetime, timedelta, timezone

import pytest

os.environ.setdefault("JWT_SECRET", "x")
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")

from ingestion import pipeline  # noqa: E402
from ingestion.scrapers import ScrapeBlockedError, booking, google_flights, kayak, mock  # noqa: E402


def test_mock_is_deterministic_per_hour_and_ids_are_stable():
    a, b = mock.flights("BOG", "MDE"), mock.flights("BOG", "MDE")
    assert [r["external_id"] for r in a] == [r["external_id"] for r in b]
    assert len(a) == 14 * 3 and len({r["external_id"] for r in a}) == len(a)
    assert all(r["arrival_at"] > r["departure_at"] and r["price"] > 0 for r in a)
    assert len(mock.hotels("CTG")) == 8 and len(mock.cars("CTG")) == 8


def test_clean_drops_invalid_rows_and_deduplicates():
    now = datetime.now(timezone.utc)
    good = {"source": "mock", "external_id": "1", "airline": "  Avianca  ", "origin": "bog", "destination": "mde",
            "departure_at": now, "arrival_at": now + timedelta(hours=1), "price": 100.0, "currency": "USD",
            "seats_available": 5}
    rows = [good, dict(good),                                              # duplicate
            {**good, "external_id": "2", "price": 0},                       # free flight -> invalid
            {**good, "external_id": "3", "origin": "BOGOTA"},               # bad IATA
            {**good, "external_id": "4", "arrival_at": now - timedelta(hours=1)}]  # arrives before departing
    out = pipeline.clean("flights", rows)
    assert len(out) == 1 and out[0]["origin"] == "BOG" and out[0]["airline"] == "Avianca"


def test_upsert_never_overwrites_live_inventory():
    for kind, column in (("flights", "seats_available"), ("hotels", "rooms_available"), ("cars", "units_available")):
        update_part = pipeline.UPSERT[kind].split("do update set")[1]
        assert column not in update_part, f"{kind} upsert must not reset {column}"


BOOKING_HTML = """
<div data-testid="property-card"><a data-testid="title-link" href="/hotel/co/foo.html?x=1">
<div data-testid="title">Hotel Foo</div></a>
<div data-testid="rating-stars"><span></span><span></span><span></span></div>
<span data-testid="price-and-discounted-price">US$ 240</span></div>
<div data-testid="property-card"><div data-testid="title">Sin precio</div></div>
"""


def test_booking_parser_extracts_cards_and_normalizes_price_per_night():
    rows = booking.parse_results(BOOKING_HTML, "MDE", nights=2)
    assert len(rows) == 1
    assert rows[0]["name"] == "Hotel Foo" and rows[0]["stars"] == 3 and rows[0]["price_per_night"] == 120.0
    assert rows[0]["source"] == "booking.com" and rows[0]["city"] == "MDE"


def test_booking_bot_wall_raises_blocked_so_prefect_can_retry():
    with pytest.raises(ScrapeBlockedError):
        booking.parse_results("<html>Verifying you are human</html>", "MDE", nights=2)


# Markup real de Booking (2026-10): cada estrella es <span><svg/></span>; la calificación oficial va en aria-label.
BOOKING_REAL_STARS = """
<div data-testid="property-card"><div data-testid="title">Hotel Bar</div>
<div aria-label="Property rating: 4 out of 5 stars"><div data-testid="rating-stars">
<span><span><svg></svg></span></span><span><span><svg></svg></span></span>
<span><span><svg></svg></span></span><span><span><svg></svg></span></span></div></div>
<span data-testid="price-and-discounted-price">US$ 300</span></div>
<div data-testid="property-card"><div data-testid="title">Sin estrellas</div>
<span data-testid="price-and-discounted-price">US$ 100</span></div>
"""


def test_booking_stars_use_official_rating_and_stay_in_1_to_5():
    rows = booking.parse_results(BOOKING_REAL_STARS, "MIA", nights=2)
    assert [r["stars"] for r in rows] == [4, None]


GOOGLE_HTML = """
<div aria-label="From 26 US dollars. Nonstop flight with LATAM. Operated by Latam Airlines Colombia. Leaves El Dorado
International Airport at 5:35 PM on Sunday, November 1 and arrives at Jose Maria Cordova International Airport
at 6:40 PM on Sunday, November 1. Total duration 1 hr 5 min.   Select flight"></div>
<div aria-label="From 1,204 US dollars. 1 stop flight with Avianca and Iberia. Leaves El Dorado International Airport
at 11:50 PM on Sunday, November 1 and arrives at Adolfo Suarez Madrid-Barajas Airport at 6:10 PM on Monday,
November 2. Total duration 12 hr 20 min."></div>
<div aria-label="From 26 US dollars"></div>
"""


def test_google_flights_parses_itinerary_labels_in_airport_time_zones():
    rows = google_flights.parse_results(GOOGLE_HTML, "BOG", "MDE", date(2026, 11, 1))
    assert rows[0]["airline"] == "LATAM" and rows[0]["price"] == 26.0 and rows[0]["source"] == "google_flights"
    assert rows[0]["departure_at"].isoformat() == "2026-11-01T17:35:00-05:00"
    madrid = google_flights.parse_results(GOOGLE_HTML, "BOG", "MAD", date(2026, 11, 1))[1]
    assert madrid["price"] == 1204.0 and madrid["arrival_at"].isoformat() == "2026-11-02T18:10:00+01:00"
    assert madrid["arrival_at"] > madrid["departure_at"]


KAYAK_FLIGHTS_HTML = """
<div aria-label="Result item 0"><div>Find great deals on eDreams</div><span>5:05 pm</span><span>-</span>
<span>6:17 pm</span><span>JetSMART</span><span>Ad</span><span>$45</span></div>
<div aria-label="Result item 1"><span>Best</span><span>8:00 am</span><span>-</span><span>9:02 am</span>
<span>Wingo</span><span>nonstop</span><span>1h 02m</span><span>$34</span><span>Economy Cabin</span></div>
<div aria-label="Result item 2"><span>11:50 pm</span><span>-</span><span>6:10 pm</span><span>+1</span>
<span>Iberia</span><span>1 stop</span><span>$1,204</span></div>
"""


def test_kayak_flights_skip_ads_and_handle_next_day_arrivals():
    rows = kayak.parse_flights(KAYAK_FLIGHTS_HTML, "BOG", "MAD", date(2026, 11, 1))
    assert [r["airline"] for r in rows] == ["Wingo", "Iberia"]
    assert rows[0]["price"] == 34.0 and rows[1]["price"] == 1204.0
    assert rows[1]["arrival_at"].isoformat() == "2026-11-02T18:10:00+01:00"


KAYAK_CARS_HTML = """
<div class="js-result"><img alt="Vehicle type: Mini - Renault Kwid or similar"><img alt="Car agency: Alamo">
<span>Expedia</span><span>$134</span><span>Total</span></div>
<div class="js-result"><img alt="Vehicle type: Economy - Class Economy Car or similar"><img alt="Car agency: Europcar">
<span>$143</span></div>
<div class="js-result"><span>sin datos</span></div>
"""


def test_kayak_cars_read_vehicle_and_agency_and_price_per_day():
    rows = kayak.parse_cars(KAYAK_CARS_HTML, "MDE", days=2)
    assert [(r["provider"], r["model"], r["category"], r["price_per_day"]) for r in rows] == [
        ("Alamo", "Renault Kwid", "mini", 67.0), ("Europcar", "Economy Car", "economy", 71.5)]
    with pytest.raises(ScrapeBlockedError):
        kayak.parse_cars("<html>captcha</html>", "MDE", days=2)
