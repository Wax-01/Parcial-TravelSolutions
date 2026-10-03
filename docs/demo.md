# Guion de la demostración en vivo (≈ 10 min)

Antes de empezar: `docker compose up --build -d`, esperar ~1 min a que todo esté *healthy* (`docker compose ps`) y abrir
en pestañas: http://localhost:8080 (app), http://localhost:4200 (Prefect), http://localhost:8787 (Dask).

## (a) Prefect monitoreando los flujos

1. En Prefect → **Deployments** están `scheduled-sync` (cada 30 min) y `demo-with-retries`.
2. Lanza `demo-with-retries` (botón *Run* o `docker compose exec prefect-runner prefect deployment run 'sync-travel-data/demo-with-retries'`).
3. En **Flow Runs → (run) → Task Runs** se ven los 28 `scrape-partition` pasar por *Retrying*: cada uno falla 2 veces
   (fallo de red inyectado) y se completa en el 3.er intento; luego `clean-normalize` y `upsert-supabase`.
   Política: `retries=3`, espera 5/15/45 s con jitter. Evidencia: `docs/evidencias/prefect-retries.txt`.
4. Comenta el run de `scheduled-sync` con `mode=auto`: cada `scrape-partition` abre un Chromium headless (Scrapling) en
   un worker y registra de dónde salieron los datos, p. ej. `scraped 25 hotels for MDE from booking.com`,
   `scraped 120 flights for BOG-MDE from google_flights+kayak`, `scraped 14 cars for MDE from kayak`. Si una fuente
   bloqueara, reintentan y solo tras el último intento caen a datos sintéticos.

## (b) Tareas distribuidas en Dask

Durante el run anterior, en http://localhost:8787 → **Status/Workers**: 2 workers con tareas en paralelo y uso de
memoria; **Graph/Task Stream** muestra las particiones (12 rutas + 8 ciudades × hoteles + 8 × autos) repartidas.

## (c) Consumo GraphQL desde el frontend

1. http://localhost:8080 → **Crear cuenta** (contraseña ≥ 10 caracteres).
2. Buscar BOG → MDE (fecha por defecto). Abre el panel *Consulta GraphQL enviada*: solo pide los campos que la UI muestra.
3. (Opcional) Mostrar que el Gateway solo pide esas columnas a la BD:
   `docker compose logs gateway | findstr "pg_graphql query"` → `node { id airline departure_at price seats_available }`.
4. GraphiQL en http://localhost:8080/graphql: probar `{ flights(origin:"BOG",destination:"MDE",first:2){ edges{ node{ id price } } pageInfo{ endCursor hasNextPage } } }`.

5. En la portada, **Paquetes destacados** muestra precios reales (vuelo + 3 noches + auto) con la etiqueta de la
   fuente; un clic llena el buscador. Más abajo, **Datos en vivo** resume cuántos registros trajo cada scraper
   (`dataSources`) y enlaza a Prefect y Dask.

## (d) Fallo transaccional y compensaciones SAGA

1. Elige un vuelo, hotel y auto. **Camino feliz:** *Simular fallo = Ninguno* → *Reservar paquete*. La línea de tiempo
   muestra `FLIGHT ✔ → HOTEL ✔ → CAR ✔ → PAYMENT ✔` y la orden queda **Confirmada**.
2. **Compensación:** *Falla la reserva del auto* → `FLIGHT ✔ → HOTEL ✔ → CAR ✘` y luego `Compensar HOTEL ✔ → Compensar FLIGHT ✔`;
   la orden queda **Cancelada (compensada)**. Verificar en BD que no quedan reservas activas ni pagos:
   ```sql
   select status from wandersync.flight_reservations where order_id = '<id>';   -- CANCELLED
   select status from wandersync.hotel_reservations  where order_id = '<id>';   -- CANCELLED
   select count(*) from wandersync.car_reservations   where order_id = '<id>';  -- 0
   select count(*) from wandersync.payments           where order_id = '<id>';  -- 0
   ```
3. **Reintentos de compensación:** *Falla el auto + la compensación del hotel falla 2 veces*: en la línea de tiempo aparecen
   dos filas `Compensar HOTEL · FAILED` (intentos 1 y 2) y un éxito en el 3.er intento, sin intervención manual.
4. Otros: *Pago rechazado* (se compensan los 3 pasos), *Timeout del servicio de autos* (fallo ambiguo: también se
   compensa el paso dudoso).
5. **Resiliencia extra:** `docker compose restart orders` durante una saga → tras ~2 min el reaper revierte la saga huérfana (probado en `scripts/e2e_recovery.py`).

## Seguridad en vivo (opcional, 2 min)

- Rate limiting: intentar iniciar sesión mal 6 veces seguidas → la UI muestra «Demasiados intentos» (HTTP 429 con `Retry-After`).
- Session Fixation: `python scripts/e2e.py` imprime `[PASS] session id REGENERATED on login` y
  `[PASS] pre-login session id no longer authenticates`.
- Auditoría: `docs/seguridad/pip-audit.txt` y `npm-audit.txt` (0 vulnerabilidades).

Comando de verificación completa: `python scripts/e2e.py` (23 comprobaciones).
