-- Trazabilidad de la ingesta en la capa de lectura: de qué fuente viene cada registro y cuándo se actualizó.
-- CREATE OR REPLACE VIEW solo agrega columnas al final: los permisos ya concedidos se conservan.
create or replace view public.ws_catalog_flights as
    select id, airline, origin, destination, departure_at, arrival_at,
           price::float8 as price, currency, seats_available, source, fetched_at
    from wandersync.flights where seats_available > 0;

create or replace view public.ws_catalog_hotels as
    select id, name, city, stars, price_per_night::float8 as price_per_night,
           currency, rooms_available, source, fetched_at
    from wandersync.hotels where rooms_available > 0;

create or replace view public.ws_catalog_cars as
    select id, provider, model, category, city, price_per_day::float8 as price_per_day,
           currency, units_available, source, fetched_at
    from wandersync.cars where units_available > 0;

-- Resumen por fuente (Booking.com, Google Flights, KAYAK, sintética) para mostrar el scraping en el frontend.
create or replace view public.ws_catalog_sources as
    select kind || ':' || source as id, kind, source, count(*)::int as items,
           max(fetched_at) as last_fetched_at, min(price)::float8 as min_price
    from (
        select 'flights' as kind, source, fetched_at, price from wandersync.flights
        union all select 'hotels', source, fetched_at, price_per_night from wandersync.hotels
        union all select 'cars', source, fetched_at, price_per_day from wandersync.cars
    ) t
    group by kind, source;

comment on view public.ws_catalog_sources is e'@graphql({"primary_key_columns": ["id"]})';
revoke all on public.ws_catalog_sources from public, anon, authenticated;
grant select on public.ws_catalog_sources to wandersync_reader;
