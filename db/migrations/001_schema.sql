-- WanderSync: esquema aislado (la BD compartida ya tiene otras tablas en `public`).
create schema if not exists wandersync;
set search_path = wandersync;

create table if not exists users (
    id            uuid primary key default gen_random_uuid(),
    email         text not null unique,
    password_hash text not null,
    created_at    timestamptz not null default now()
);

-- Catálogo (alimentado por la ingesta Dask/Prefect)
create table if not exists flights (
    id               uuid primary key default gen_random_uuid(),
    source           text not null,
    external_id      text not null,
    airline          text not null,
    origin           text not null,
    destination      text not null,
    departure_at     timestamptz not null,
    arrival_at       timestamptz not null,
    price            numeric(12,2) not null check (price >= 0),
    currency         text not null default 'USD',
    seats_available  integer not null check (seats_available >= 0),
    fetched_at       timestamptz not null default now(),
    unique (source, external_id)
);
create index if not exists flights_route_idx on flights (origin, destination, departure_at);

create table if not exists hotels (
    id                uuid primary key default gen_random_uuid(),
    source            text not null,
    external_id       text not null,
    name              text not null,
    city              text not null,
    stars             integer check (stars between 1 and 5),
    price_per_night   numeric(12,2) not null check (price_per_night >= 0),
    currency          text not null default 'USD',
    rooms_available   integer not null check (rooms_available >= 0),
    fetched_at        timestamptz not null default now(),
    unique (source, external_id)
);
create index if not exists hotels_city_idx on hotels (city);

create table if not exists cars (
    id               uuid primary key default gen_random_uuid(),
    source           text not null,
    external_id      text not null,
    provider         text not null,
    model            text not null,
    category         text not null default 'economy',
    city             text not null,
    price_per_day    numeric(12,2) not null check (price_per_day >= 0),
    currency         text not null default 'USD',
    units_available  integer not null check (units_available >= 0),
    fetched_at       timestamptz not null default now(),
    unique (source, external_id)
);
create index if not exists cars_city_idx on cars (city);

-- Órdenes y SAGA
create table if not exists orders (
    id               uuid primary key default gen_random_uuid(),
    user_id          uuid not null references users(id),
    status           text not null default 'PENDING'
                     check (status in ('PENDING','CONFIRMED','COMPENSATING','CANCELLED','COMPENSATION_FAILED')),
    flight_id        uuid not null references flights(id),
    hotel_id         uuid not null references hotels(id),
    car_id           uuid not null references cars(id),
    nights           integer not null check (nights between 1 and 60),
    total_amount     numeric(12,2),
    currency         text not null default 'USD',
    failure_reason   text,
    simulate_failure text,
    idempotency_key  text,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    unique (user_id, idempotency_key)
);
create index if not exists orders_user_idx on orders (user_id, created_at desc);

create table if not exists saga_log (
    id          bigserial primary key,
    order_id    uuid not null references orders(id) on delete cascade,
    step        text not null,                     -- FLIGHT | HOTEL | CAR | PAYMENT
    action      text not null check (action in ('EXECUTE','COMPENSATE')),
    status      text not null check (status in ('STARTED','SUCCEEDED','FAILED')),
    detail      text,
    created_at  timestamptz not null default now()
);
create index if not exists saga_log_order_idx on saga_log (order_id, id);

-- Reservas: cada servicio es dueño de su tabla (idempotentes por order_id)
create table if not exists flight_reservations (
    id          uuid primary key default gen_random_uuid(),
    order_id    uuid not null unique references orders(id),
    flight_id   uuid not null references flights(id),
    seats       integer not null default 1,
    status      text not null default 'CONFIRMED' check (status in ('CONFIRMED','CANCELLED')),
    created_at  timestamptz not null default now(),
    cancelled_at timestamptz
);
create table if not exists hotel_reservations (
    id          uuid primary key default gen_random_uuid(),
    order_id    uuid not null unique references orders(id),
    hotel_id    uuid not null references hotels(id),
    nights      integer not null,
    status      text not null default 'CONFIRMED' check (status in ('CONFIRMED','CANCELLED')),
    created_at  timestamptz not null default now(),
    cancelled_at timestamptz
);
create table if not exists car_reservations (
    id          uuid primary key default gen_random_uuid(),
    order_id    uuid not null unique references orders(id),
    car_id      uuid not null references cars(id),
    days        integer not null,
    status      text not null default 'CONFIRMED' check (status in ('CONFIRMED','CANCELLED')),
    created_at  timestamptz not null default now(),
    cancelled_at timestamptz
);

create table if not exists payments (
    id          uuid primary key default gen_random_uuid(),
    order_id    uuid not null unique references orders(id),
    amount      numeric(12,2) not null,
    currency    text not null default 'USD',
    status      text not null default 'CAPTURED' check (status in ('CAPTURED','REFUNDED','FAILED')),
    created_at  timestamptz not null default now(),
    refunded_at timestamptz
);

create table if not exists ingestion_runs (
    id          bigserial primary key,
    flow_run    text,
    source      text not null,
    kind        text not null,
    rows_upserted integer not null default 0,
    status      text not null,
    finished_at timestamptz not null default now()
);

-- RLS activado en todo: los roles anon/authenticated de Supabase (PostgREST) no ven nada.
do $$
declare t text;
begin
    for t in select tablename from pg_tables where schemaname = 'wandersync' loop
        execute format('alter table wandersync.%I enable row level security', t);
    end loop;
end $$;
