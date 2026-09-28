-- Capa de lectura GraphQL: vistas de solo lectura + rol mínimo para pg_graphql.
create extension if not exists pg_graphql;
set search_path = wandersync;

-- Las vistas corren con los privilegios del dueño; el rol lector no toca tablas base.
create or replace view catalog_flights as
    select id, airline, origin, destination, departure_at, arrival_at,
           price::float8 as price, currency, seats_available
    from flights where seats_available > 0;

create or replace view catalog_hotels as
    select id, name, city, stars, price_per_night::float8 as price_per_night,
           currency, rooms_available
    from hotels where rooms_available > 0;

create or replace view catalog_cars as
    select id, provider, model, category, city, price_per_day::float8 as price_per_day,
           currency, units_available
    from cars where units_available > 0;

-- Órdenes: user_id se incluye solo para que el Gateway filtre server-side; el schema GraphQL público no lo expone.
create or replace view order_summaries as
    select id, user_id, status, flight_id, hotel_id, car_id, nights,
           total_amount::float8 as total_amount, currency, failure_reason,
           created_at, updated_at
    from orders;

create or replace view order_saga_steps as
    select s.id, s.order_id, o.user_id, s.step, s.action, s.status, s.detail, s.created_at
    from saga_log s join orders o on o.id = s.order_id;

comment on view catalog_flights   is e'@graphql({"primary_key_columns": ["id"]})';
comment on view catalog_hotels    is e'@graphql({"primary_key_columns": ["id"]})';
comment on view catalog_cars      is e'@graphql({"primary_key_columns": ["id"]})';
comment on view order_summaries   is e'@graphql({"primary_key_columns": ["id"]})';
comment on view order_saga_steps  is e'@graphql({"primary_key_columns": ["id"]})';

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'wandersync_reader') then
        create role wandersync_reader nologin noinherit;
    end if;
end $$;

-- El rol de conexión debe poder hacer SET ROLE al lector.
do $$ begin execute format('grant wandersync_reader to %I', current_user); end $$;

grant usage on schema wandersync to wandersync_reader;
grant select on catalog_flights, catalog_hotels, catalog_cars, order_summaries, order_saga_steps
    to wandersync_reader;
grant usage on schema graphql to wandersync_reader;
grant execute on function graphql.resolve(text, jsonb, text, jsonb) to wandersync_reader;
