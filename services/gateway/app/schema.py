"""GraphQL schema. Reads are delegated to Supabase pg_graphql projecting only requested columns;
writes go to the Orders service (which runs the SAGA)."""
import asyncio
import enum
import logging
import re
import uuid
from datetime import date, timedelta
from typing import Annotated, Generic, Optional, TypeVar, Union
from uuid import UUID

import httpx
import psycopg.errors
import strawberry
from graphql import GraphQLError
from strawberry.types import Info

from wandersync_common.config import env, env_bool, internal_token

from . import security
from .graphql_db import resolve
from .projection import (Node, collection_fragment, columns, connection_selection, flatten)
from .state import Ctx, state

log = logging.getLogger("gateway.schema")

T = TypeVar("T")
IATA = re.compile(r"^[A-Z]{3}$")
EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


# ---------------------------------------------------------------- types
@strawberry.type
class PageInfo:
    has_next_page: bool
    end_cursor: Optional[str]


@strawberry.type
class Edge(Generic[T]):
    node: T
    cursor: str


@strawberry.type
class Connection(Generic[T]):
    edges: list[Edge[T]]
    page_info: PageInfo


@strawberry.type
class Flight:
    id: strawberry.ID
    airline: str
    origin: str
    destination: str
    departure_at: str
    arrival_at: str
    price: float
    currency: str
    seats_available: int
    source: str
    fetched_at: str


@strawberry.type
class Hotel:
    id: strawberry.ID
    name: str
    city: str
    stars: Optional[int]
    price_per_night: float
    currency: str
    rooms_available: int
    source: str
    fetched_at: str


@strawberry.type
class Car:
    id: strawberry.ID
    provider: str
    model: str
    category: str
    city: str
    price_per_day: float
    currency: str
    units_available: int
    source: str
    fetched_at: str


@strawberry.type
class DataSource:
    """What the ingestion pipeline (Prefect + Dask + scrapers) has loaded, per kind and source."""
    id: strawberry.ID
    kind: str
    source: str
    items: int
    last_fetched_at: Optional[str]
    min_price: Optional[float]


@strawberry.type
class PackageSearchResult:
    flights: Connection[Flight]
    hotels: Connection[Hotel]
    cars: Connection[Car]


@strawberry.type
class SagaStep:
    id: strawberry.ID
    step: str
    action: str
    status: str
    detail: Optional[str]
    created_at: str


@strawberry.type
class Order:
    id: strawberry.ID
    status: str
    nights: int
    flight_id: strawberry.ID
    hotel_id: strawberry.ID
    car_id: strawberry.ID
    total_amount: Optional[float]
    currency: str
    failure_reason: Optional[str]
    created_at: str
    updated_at: str
    steps: list[SagaStep]


@strawberry.type
class User:
    id: strawberry.ID
    email: str


@strawberry.type
class ApiError:
    code: str
    message: str


@strawberry.type
class BookingAccepted:
    order_id: strawberry.ID
    status: str
    idempotent: bool


AuthResult = Annotated[Union[User, ApiError], strawberry.union("AuthResult")]
BookingResult = Annotated[Union[BookingAccepted, ApiError], strawberry.union("BookingResult")]


@strawberry.enum
class SimulatedFailure(enum.Enum):
    FLIGHT = "FLIGHT"
    HOTEL = "HOTEL"
    CAR = "CAR"
    PAYMENT = "PAYMENT"
    CAR_TIMEOUT = "CAR_TIMEOUT"
    CAR_FLAKY_COMPENSATION = "CAR_FLAKY_COMPENSATION"


@strawberry.input
class CreateBookingInput:
    flight_id: UUID
    hotel_id: UUID
    car_id: UUID
    nights: int
    idempotency_key: Optional[str] = None
    simulate_failure: Optional[SimulatedFailure] = None


# ---------------------------------------------------------------- helpers
def _first(n: int) -> int:
    return max(1, min(n, 30))


def _validate_iata(value: str, field: str) -> str:
    value = value.strip().upper()
    if not IATA.match(value):
        raise GraphQLError(f"{field} debe ser un código de 3 letras (ej. BOG)")
    return value


def _day_bounds(day: str) -> tuple[str, str]:
    try:
        d = date.fromisoformat(day)
    except ValueError:
        raise GraphQLError("La fecha debe tener formato AAAA-MM-DD")
    return f"{d.isoformat()}T00:00:00Z", f"{(d + timedelta(days=1)).isoformat()}T00:00:00Z"


REAL_ONLY = {"source": {"neq": "mock"}}


def _flight_args(origin: str, destination: str, day: Optional[str], max_price: Optional[float]):
    filt: dict = {"origin": {"eq": origin}, "destination": {"eq": destination}}
    if day:
        start, end = _day_bounds(day)
        filt["departure_at"] = {"gte": start, "lt": end}
    if max_price is not None:
        filt["price"] = {"lte": max_price}
    return filt, [{"price": "AscNullsLast"}]


def _hotel_args(city: str, max_price: Optional[float], min_stars: Optional[int]):
    filt: dict = {"city": {"eq": city}}
    if max_price is not None:
        filt["price_per_night"] = {"lte": max_price}
    if min_stars is not None:
        filt["stars"] = {"gte": min_stars}
    return filt, [{"price_per_night": "AscNullsLast"}]


def _car_args(city: str, category: Optional[str], max_price: Optional[float]):
    filt: dict = {"city": {"eq": city}}
    if category:
        filt["category"] = {"eq": category.lower()}
    if max_price is not None:
        filt["price_per_day"] = {"lte": max_price}
    return filt, [{"price_per_day": "AscNullsLast"}]


async def _run(fragments: list[tuple[str, list[str], dict]]) -> dict:
    """Single round-trip to pg_graphql for one or many aliased collections."""
    body = " ".join(f[0] for f in fragments)
    defs = ", ".join(d for f in fragments for d in f[1])
    variables: dict = {}
    for f in fragments:
        variables.update(f[2])
    return await resolve(state.pool, f"query({defs}) {{ {body} }}", variables)


def _need_user(info: Info) -> UUID:
    ctx: Ctx = info.context
    if not ctx.session.user_id:
        raise GraphQLError("Debes iniciar sesión")
    return ctx.session.user_id


# ---------------------------------------------------------------- queries
@strawberry.type
class Query:
    @strawberry.field
    async def flights(self, info: Info, origin: str, destination: str, date: Optional[str] = None,
                      max_price: Optional[float] = None, first: int = 20,
                      after: Optional[str] = None) -> Connection[Flight]:
        origin, destination = _validate_iata(origin, "origin"), _validate_iata(destination, "destination")
        filt, order = _flight_args(origin, destination, date, max_price)
        sel = connection_selection(info.selected_fields[0])
        frag = collection_fragment("r", "ws_catalog_flightsCollection", "f", sel, filter_=filt, order_by=order,
                                   first=_first(first), after=after, entity="ws_catalog_flights")
        return Node((await _run([frag]))["r"])

    @strawberry.field
    async def hotels(self, info: Info, city: str, max_price_per_night: Optional[float] = None,
                     min_stars: Optional[int] = None, first: int = 20,
                     after: Optional[str] = None) -> Connection[Hotel]:
        city = _validate_iata(city, "city")
        filt, order = _hotel_args(city, max_price_per_night, min_stars)
        sel = connection_selection(info.selected_fields[0])
        frag = collection_fragment("r", "ws_catalog_hotelsCollection", "h", sel, filter_=filt, order_by=order,
                                   first=_first(first), after=after, entity="ws_catalog_hotels")
        return Node((await _run([frag]))["r"])

    @strawberry.field
    async def cars(self, info: Info, city: str, category: Optional[str] = None,
                   max_price_per_day: Optional[float] = None, first: int = 20,
                   after: Optional[str] = None) -> Connection[Car]:
        city = _validate_iata(city, "city")
        filt, order = _car_args(city, category, max_price_per_day)
        sel = connection_selection(info.selected_fields[0])
        frag = collection_fragment("r", "ws_catalog_carsCollection", "c", sel, filter_=filt, order_by=order,
                                   first=_first(first), after=after, entity="ws_catalog_cars")
        return Node((await _run([frag]))["r"])

    @strawberry.field
    async def search_packages(self, info: Info, origin: str, destination: str, depart_date: str,
                              nights: int = 3, first: int = 5, real_only: bool = False) -> PackageSearchResult:
        """Consolidated availability: flights + hotels + cars in ONE pg_graphql round-trip, and only for
        the sub-selections the client actually asked for. real_only excludes the synthetic fallback source."""
        extra = REAL_ONLY if real_only else {}
        origin, destination = _validate_iata(origin, "origin"), _validate_iata(destination, "destination")
        if not 1 <= nights <= 60:
            raise GraphQLError("nights debe estar entre 1 y 60")
        wanted = {f.name: f for f in flatten(info.selected_fields[0].selections)}
        fragments = []
        if "flights" in wanted:
            filt, order = _flight_args(origin, destination, depart_date, None)
            filt = {**filt, **extra}
            fragments.append(collection_fragment(
                "flights", "ws_catalog_flightsCollection", "f", connection_selection(wanted["flights"]),
                filter_=filt, order_by=order, first=_first(first), after=None, entity="ws_catalog_flights"))
        if "hotels" in wanted:
            filt, order = _hotel_args(destination, None, None)
            filt = {**filt, **extra}
            fragments.append(collection_fragment(
                "hotels", "ws_catalog_hotelsCollection", "h", connection_selection(wanted["hotels"]),
                filter_=filt, order_by=order, first=_first(first), after=None, entity="ws_catalog_hotels"))
        if "cars" in wanted:
            filt, order = _car_args(destination, None, None)
            filt = {**filt, **extra}
            fragments.append(collection_fragment(
                "cars", "ws_catalog_carsCollection", "c", connection_selection(wanted["cars"]),
                filter_=filt, order_by=order, first=_first(first), after=None, entity="ws_catalog_cars"))
        return Node(await _run(fragments) if fragments else {})

    @strawberry.field
    async def data_sources(self, info: Info) -> list[DataSource]:
        """Ingestion summary per source (items, last update, cheapest price): public catalog metadata."""
        cols = sorted(set(columns(info.selected_fields[0].selections)) | {"id"})
        frag = collection_fragment("d", "ws_catalog_sourcesCollection", "d", "{ edges { node { " + " ".join(cols) + " } } }",
                                   filter_=None, order_by=[{"items": "DescNullsLast"}], first=30, after=None,
                                   entity="ws_catalog_sources")
        return [Node(e["node"]) for e in (await _run([frag]))["d"]["edges"]]

    @strawberry.field
    async def order(self, info: Info, id: UUID) -> Optional[Order]:
        user_id = _need_user(info)
        rows = await _load_orders(info, {"id": {"eq": str(id)}, "user_id": {"eq": str(user_id)}}, first=1)
        return rows[0] if rows else None

    @strawberry.field
    async def my_orders(self, info: Info, first: int = 10) -> list[Order]:
        user_id = _need_user(info)
        return await _load_orders(info, {"user_id": {"eq": str(user_id)}}, first=_first(first))

    @strawberry.field
    async def me(self, info: Info) -> Optional[User]:
        ctx: Ctx = info.context
        if not ctx.session.user_id:
            return None
        async with state.pool.connection() as conn:
            row = await (await conn.execute(
                "select id, email from wandersync.users where id=%s", (ctx.session.user_id,))).fetchone()
        return User(id=strawberry.ID(str(row["id"])), email=row["email"]) if row else None


async def _load_orders(info: Info, filt: dict, first: int) -> list[Node]:
    """Orders (+ their SAGA steps if requested). user_id filtering is injected server-side, never by the client."""
    fields = flatten(info.selected_fields[0].selections)
    cols = columns(info.selected_fields[0].selections, exclude={"steps"})
    cols = sorted(set(cols) | {"id"})
    step_field = next((f for f in fields if f.name == "steps"), None)
    frag = collection_fragment("o", "ws_order_summariesCollection", "o", "{ edges { node { " + " ".join(cols) + " } } }",
                               filter_=filt, order_by=[{"created_at": "DescNullsLast"}], first=first, after=None,
                               entity="ws_order_summaries")
    data = await _run([frag])
    orders = [e["node"] for e in data["o"]["edges"]]
    if step_field and orders:
        scols = sorted(set(columns(step_field.selections)) | {"order_id", "id"})
        sfrag = collection_fragment(
            "s", "ws_order_saga_stepsCollection", "s", "{ edges { node { " + " ".join(scols) + " } } }",
            filter_={"user_id": {"eq": filt["user_id"]["eq"]}, "order_id": {"in": [o["id"] for o in orders]}},
            order_by=[{"id": "AscNullsLast"}], first=30, after=None, entity="ws_order_saga_steps")
        steps = [e["node"] for e in (await _run([sfrag]))["s"]["edges"]]
        for o in orders:
            o["steps"] = [s for s in steps if s["order_id"] == o["id"]]
    else:
        for o in orders:
            o["steps"] = []
    return [Node(o) for o in orders]


# ---------------------------------------------------------------- mutations
def _set_session_cookie(ctx: Ctx) -> None:
    ctx.response.set_cookie(
        "ws_session", state.sessions.encode(ctx.session.id), max_age=state.sessions.ttl, httponly=True,
        samesite="lax", secure=env_bool("COOKIE_SECURE", False), path="/")


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def register(self, info: Info, email: str, password: str) -> AuthResult:
        ctx: Ctx = info.context
        email = email.strip().lower()
        if not EMAIL.match(email) or len(email) > 254:
            return ApiError(code="INVALID_EMAIL", message="Correo electrónico inválido")
        if not 10 <= len(password) <= 128:
            return ApiError(code="WEAK_PASSWORD", message="La contraseña debe tener entre 10 y 128 caracteres")
        pw_hash = await asyncio.to_thread(security.hash_password, password)
        try:
            async with state.pool.connection() as conn:
                row = await (await conn.execute(
                    "insert into wandersync.users(email, password_hash) values (%s,%s) returning id",
                    (email, pw_hash))).fetchone()
        except psycopg.errors.UniqueViolation:
            return ApiError(code="REGISTRATION_FAILED",
                            message="No se pudo completar el registro. Si ya tienes cuenta, inicia sesión.")
        ctx.session = await state.sessions.regenerate(ctx.session, row["id"])
        _set_session_cookie(ctx)
        return User(id=strawberry.ID(str(row["id"])), email=email)

    @strawberry.mutation
    async def login(self, info: Info, email: str, password: str) -> AuthResult:
        ctx: Ctx = info.context
        email = email.strip().lower()
        async with state.pool.connection() as conn:
            user = await (await conn.execute(
                "select id, email, password_hash from wandersync.users where email=%s", (email,))).fetchone()
        if not await asyncio.to_thread(security.verify_password, user["password_hash"] if user else None, password):
            return ApiError(code="INVALID_CREDENTIALS", message="Correo o contraseña incorrectos")
        if security.needs_rehash(user["password_hash"]):
            async with state.pool.connection() as conn:
                await conn.execute("update wandersync.users set password_hash=%s where id=%s",
                                   (await asyncio.to_thread(security.hash_password, password), user["id"]))
        # Session Fixation defence: a NEW session id is issued and the pre-login one is destroyed.
        ctx.session = await state.sessions.regenerate(ctx.session, user["id"])
        _set_session_cookie(ctx)
        return User(id=strawberry.ID(str(user["id"])), email=user["email"])

    @strawberry.mutation
    async def logout(self, info: Info) -> bool:
        ctx: Ctx = info.context
        await state.sessions.destroy(ctx.session)
        ctx.response.delete_cookie("ws_session", path="/")
        return True

    @strawberry.mutation
    async def create_booking(self, info: Info, input: CreateBookingInput) -> BookingResult:
        ctx: Ctx = info.context
        if not ctx.session.user_id:
            return ApiError(code="UNAUTHENTICATED", message="Debes iniciar sesión para reservar")
        if not 1 <= input.nights <= 60:
            return ApiError(code="INVALID_INPUT", message="nights debe estar entre 1 y 60")
        payload = {
            "user_id": str(ctx.session.user_id), "flight_id": str(input.flight_id),
            "hotel_id": str(input.hotel_id), "car_id": str(input.car_id), "nights": input.nights,
            "idempotency_key": (input.idempotency_key or uuid.uuid4().hex)[:100],
            "simulate_failure": input.simulate_failure.value if input.simulate_failure else None,
        }
        try:
            resp = await state.http.post(f"{env('ORDERS_URL')}/orders", json=payload,
                                         headers={"X-Internal-Token": internal_token()})
        except httpx.HTTPError:
            log.exception("orders service unreachable")
            return ApiError(code="UPSTREAM_UNAVAILABLE", message="Servicio de órdenes no disponible")
        if resp.status_code == 422:
            return ApiError(code="INVALID_ITEMS", message="Vuelo, hotel o auto inexistente")
        if resp.status_code >= 400:
            log.error("orders replied %s: %s", resp.status_code, resp.text[:200])
            return ApiError(code="BOOKING_FAILED", message="No se pudo crear la reserva")
        body = resp.json()
        return BookingAccepted(order_id=strawberry.ID(body["id"]), status=body["status"],
                               idempotent=body["idempotent"])
