import os
from datetime import datetime, timedelta, timezone

import pytest

os.environ.setdefault("JWT_SECRET", "x")
os.environ.setdefault("DATABASE_URL", "postgresql://u:p@localhost/db")

from ingestion import pipeline  # noqa: E402
from ingestion.scrapers import ScrapeBlockedError, booking, mock  # noqa: E402


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
