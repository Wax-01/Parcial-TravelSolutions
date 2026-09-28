# WanderSync Travel Solutions

Plataforma de paquetes turísticos dinámicos (vuelo + hotel + auto) con **API Gateway GraphQL**, **patrón SAGA**
(orquestación), ingesta distribuida con **Dask**, orquestación/observabilidad con **Prefect** y ciberseguridad por diseño.
Todo se levanta con un único comando.

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
make test    # 8 (SAGA) + 11 (gateway) + 5 (ingesta) pruebas unitarias
make e2e     # 22 comprobaciones contra el stack real (sesiones, SAGA, compensaciones, 429)
make audit   # pip-audit por servicio + npm audit  ->  docs/seguridad/
python scripts/e2e_recovery.py   # mata el orquestador a mitad de una saga y verifica la reversión automática (~2.5 min)
```

## Documentación

- [Arquitectura y decisiones](docs/arquitectura.md) (diagramas Mermaid, secuencias SAGA feliz y con compensación)
- [Guion de la demostración en vivo](docs/demo.md)
- [Ciberseguridad y auditoría de dependencias](docs/seguridad/README.md)
- Evidencias: [`e2e.txt`](docs/evidencias/e2e.txt), [`e2e_recovery.txt`](docs/evidencias/e2e_recovery.txt), [`prefect-retries.txt`](docs/evidencias/prefect-retries.txt)

## Limitaciones conocidas (leer antes de la demo)

- **Scraping real:** Booking.com responde con un reto anti-bot (HTTP 202). El scraper real está implementado y sus
  reintentos son visibles en Prefect; tras agotarlos, `SCRAPER_MODE=auto` usa datos sintéticos deterministas
  (`source = 'mock'`). Vuelos y autos usan siempre la fuente sintética. Ver `docs/arquitectura.md`.
- **BD compartida:** la base de Supabase del proyecto ya contenía tablas de otra aplicación en `public`. WanderSync vive
  en el esquema `wandersync` y solo publica en `public` cinco vistas de solo lectura con prefijo `ws_`.
- **Simulación de fallos** (`ALLOW_FAILURE_SIMULATION=true`) está pensada para la demo; desactivar en producción.
