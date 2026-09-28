-- pg_graphql (Supabase) solo expone `public`. Publicamos ÚNICAMENTE vistas de solo lectura con prefijo ws_.
-- Las tablas base viven en `wandersync` (no expuesto). No se modifica nada existente en `public`.
drop view if exists wandersync.catalog_flights, wandersync.catalog_hotels, wandersync.catalog_cars,
                    wandersync.order_summaries, wandersync.order_saga_steps;

create or replace view public.ws_catalog_flights as
    select id, airline, origin, destination, departure_at, arrival_at,
           price::float8 as price, currency, seats_available
    from wandersync.flights where seats_available > 0;

create or replace view public.ws_catalog_hotels as
    select id, name, city, stars, price_per_night::float8 as price_per_night,
           currency, rooms_available
    from wandersync.hotels where rooms_available > 0;

create or replace view public.ws_catalog_cars as
    select id, provider, model, category, city, price_per_day::float8 as price_per_day,
           currency, units_available
    from wandersync.cars where units_available > 0;

-- user_id se filtra SIEMPRE server-side en el Gateway (nunca lo controla el cliente).
create or replace view public.ws_order_summaries as
    select id, user_id, status, flight_id, hotel_id, car_id, nights,
           total_amount::float8 as total_amount, currency, failure_reason, created_at, updated_at
    from wandersync.orders;

create or replace view public.ws_order_saga_steps as
    select s.id, s.order_id, o.user_id, s.step, s.action, s.status, s.detail, s.created_at
    from wandersync.saga_log s join wandersync.orders o on o.id = s.order_id;

do $$
declare v text;
begin
    foreach v in array array['ws_catalog_flights','ws_catalog_hotels','ws_catalog_cars','ws_order_summaries','ws_order_saga_steps'] loop
        execute format('comment on view public.%I is %L', v, '@graphql({"primary_key_columns": ["id"]})');
        -- Supabase concede ALL a anon/authenticated por defecto: se revoca explícitamente.
        execute format('revoke all on public.%I from public, anon, authenticated', v);
        execute format('grant select on public.%I to wandersync_reader', v);
    end loop;
end $$;
