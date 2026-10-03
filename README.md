# WanderSync Travel Solutions

Plataforma de paquetes turísticos dinámicos (vuelo + hotel + auto) con **API Gateway GraphQL**, **patrón SAGA**
(orquestación), ingesta distribuida con **Dask**, orquestación/observabilidad con **Prefect** y ciberseguridad por diseño.
Todo se levanta con un único comando. En el frontend la marca es **TravelSolutions**; los nombres internos
(esquema `wandersync`, cookie `ws_session`) se conservan.

## Inicio rápido

```bash
cp .env.example .env        # completar DATABASE_URL (Supabase) y JWT_SECRET
docker compose up --build   # ~1-2 min la primera vez
```

| URL | Qué es |
|---|---|
| http://localhost:8080 | Frontend (React) y único punto de entrada: `/graphql` proxea al Gateway |
| http://localhost:8080/graphql | GraphiQL del Gateway (introspección activa en demo) |
| http://localhost:4200 | Panel de **Prefect** (flows, reintentos, logs) |
| http://localhost:8787 | Dashboard de **Dask** (workers y tareas) |

Las migraciones se aplican solas (servicio `migrate`) en el esquema aislado `wandersync` de la BD Supabase,
y el runner de Prefect ejecuta una primera ingesta al arrancar.

## Estructura

```
services/{gateway,flights,hotels,cars,orders}/  FastAPI (+Strawberry en gateway), Dockerfile y requirements con hashes
ingestion/                                      Flow de Prefect, scrapers, limpieza y upsert (imagen usada por Prefect y Dask)
frontend/                                       React + Vite + Apollo Client servido con nginx
db/                                             Migraciones SQL y runner
libs/common/                                    Utilidades compartidas (pool DB, token interno, inyección de fallos)
scripts/                                        e2e.py (verificación), audit.sh (supply chain)
docs/                                           Arquitectura, SAGA, demo, seguridad y evidencias
```

## Verificación

```bash
pip install -r requirements-dev.txt
make test    # 8 (SAGA) + 13 (gateway) + 9 (ingesta) pruebas unitarias
make e2e     # 23 comprobaciones contra el stack real (sesiones, SAGA, compensaciones, reserva tardía, 429)
make audit   # pip-audit por servicio + npm audit  ->  docs/seguridad/
python scripts/e2e_recovery.py   # mata el orquestador a mitad de una saga y verifica la reversión automática (~2.5 min)
```

## Documentación

- [Arquitectura y decisiones](docs/arquitectura.md) (diagramas Mermaid, secuencias SAGA feliz y con compensación)
- [Guion de la demostración en vivo](docs/demo.md)
- [Ciberseguridad y auditoría de dependencias](docs/seguridad/README.md)
- Evidencias: [`e2e.txt`](docs/evidencias/e2e.txt), [`e2e_recovery.txt`](docs/evidencias/e2e_recovery.txt), [`prefect-retries.txt`](docs/evidencias/prefect-retries.txt)

## Limitaciones conocidas (leer antes de la demo)

- **Scraping real:** hoteles de Booking.com, vuelos de Google Flights y KAYAK, y autos de KAYAK, renderizados con
  [Scrapling](https://github.com/D4Vinci/Scrapling) (`DynamicFetcher`, Chromium headless). Los vuelos reales cubren los
  días `SCRAPE_FLIGHT_DAYS` (por defecto hoy+3 y hoy+7). Si una fuente bloquea o cambia su markup, Prefect reintenta y
  `SCRAPER_MODE=auto` cae a datos sintéticos (`source = 'mock'`). Ver `docs/arquitectura.md`.
- **BD compartida:** la base de Supabase del proyecto ya contenía tablas de otra aplicación en `public`. WanderSync vive
  en el esquema `wandersync` y solo publica en `public` cinco vistas de solo lectura con prefijo `ws_`.
- **Simulación de fallos** (`ALLOW_FAILURE_SIMULATION=true`) está pensada para la demo; desactivar en producción.
