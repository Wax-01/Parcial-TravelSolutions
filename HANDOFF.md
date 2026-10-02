# Handoff: estado del trabajo (2026-10-02)

Rama: `feat/scraping-real-y-ui` (aún no está mergeada a `master`).

## Ya hecho y verificado

- **Frontend rediseñado** (estilo Behance "Travel & Tourism"): hero, destinos, resultados con foto, tarjeta de paquete,
  SAGA como itinerario. Fuentes e imágenes locales (respeta la CSP de nginx).
- **Scraping real con Scrapling** (`DynamicFetcher`, Chromium headless), helper `ingestion/scrapers/render`:
  - Hoteles: Booking.com. Vuelos: Google Flights + KAYAK. Autos: KAYAK.
  - Última sync completa: 28/28 particiones reales, 0 caídas a datos sintéticos, ~12 min
    (1228 vuelos, 169 hoteles, 116 autos reales en Supabase).
- e2e 22/22, tests de ingesta 9/9, pip-audit y npm audit sin vulnerabilidades.

## A medio hacer (en este commit, revisar)

1. **Migración `db/migrations/005_catalog_sources.sql`** (YA aplicada en Supabase): agrega `source` y `fetched_at` a las
   vistas `ws_catalog_*` y crea `ws_catalog_sources` (conteo, última actualización y precio mínimo por fuente).
2. **Gateway** (`services/gateway/app/schema.py`), probado (13/13 tests, consultas reales OK):
   - `source` y `fetchedAt` en `Flight`, `Hotel` y `Car`.
   - `searchPackages(..., realOnly: true)` excluye la fuente sintética.
   - Nueva consulta `dataSources { kind source items lastFetchedAt minPrice }`.
3. **KAYAK autos** (`ingestion/scrapers/kayak.py`): se descartan tarifas < USD 8/día. En Miami KAYAK publica
   tarifas base irreales ("$13 total" por 3 días, "91% cheaper"). **Este cambio y su test NO se han ejecutado
   todavía**: correr los tests de ingesta y reconstruir los workers.

## Ideas pendientes (pedidas por el equipo)

- **Renombrar la marca a "TravelSolutions"** en el frontend: logo, `<title>` en `frontend/index.html` y footer.
  Hoy dice "WanderSync". El esquema `wandersync`, la cookie `ws_session` y los nombres internos pueden quedarse.
- **Mostrar paquetes en la portada:** armar 3-4 paquetes destacados (p. ej. BOG→CTG, BOG→MDE, BOG→MIA, BOG→MAD)
  con `searchPackages(realOnly: true, first: 1)` para hoy+3. Precio = vuelo + hotel × noches + auto × noches. Al hacer
  clic se llena el buscador.
- **Hacer visible el scraping y los datos:** sección "Datos en vivo" con `dataSources` (Booking.com, Google Flights,
  KAYAK: cantidad de registros, última actualización, precio mínimo) y enlaces a Prefect (:4200) y Dask (:8787).
  Etiqueta de fuente en cada resultado (vuelo, hotel, auto).
- Agregar `source`/`fetchedAt` a la consulta `SEARCH_PACKAGES` de `frontend/src/queries.js` para las etiquetas.

## Otros pendientes / riesgos

- La sync tarda ~12 min y está programada cada 15 (`SYNC_INTERVAL_SECONDS`). Subir el intervalo o bajar
  `SCRAPE_FLIGHT_DAYS` (por defecto `3,7`) si se cruzan.
- Siguen en la BD datos sintéticos viejos (648 vuelos, 64 hoteles, 64 autos). Decidir si borrarlos (ojo: las órdenes
  referencian ids) o usar siempre `realOnly`.
- Vuelos reales solo existen para hoy+3 y hoy+7; otras fechas muestran datos sintéticos.
- Datos de prueba en Supabase: cuentas `ui-…@test.local` y órdenes de prueba; limpiar antes de la demo.
- Actualizar evidencias (`docs/evidencias/*.txt`) y la doc de arquitectura con `dataSources`/`realOnly`.
- Mergear `feat/scraping-real-y-ui` a `master` cuando esté revisado.

## Cómo verificar

```bash
docker compose up -d --build            # requiere .env con DATABASE_URL y JWT_SECRET
python scripts/e2e.py                    # 22 comprobaciones
# tests de ingesta (dentro de la imagen, por Chromium/selectolax):
docker run --rm -u root -v "$PWD/ingestion:/srv/ingestion" parcial-travelsolutions-dask-worker \
  sh -c "pip install -q pytest && python -m pytest ingestion/tests -q -p no:cacheprovider"
```
