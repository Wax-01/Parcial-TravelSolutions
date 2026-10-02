# Documento técnico de arquitectura: WanderSync Travel Solutions

## 1. Problema y objetivos

La plataforma anterior sufría **reservas huérfanas**: el pago y el vuelo se confirmaban pero el hotel o el auto fallaba,
sin reversión automática. Además, la sincronización masiva de tarifas bloqueaba la capa de persistencia.
El rediseño ataca ambos problemas con:

| Problema | Solución |
|---|---|
| Inconsistencia transaccional | **SAGA por orquestación** con compensaciones idempotentes, reintentos y recuperación automática |
| Ingesta bloqueante | **Dask** (workers paralelos) coordinado por **Prefect** (retries + observabilidad), desacoplado del tráfico de usuarios |
| Over-fetching / acoplamiento del frontend | **API Gateway GraphQL** único; lecturas delegadas a `pg_graphql` con proyección de columnas |
| Seguridad | Argon2id, sesiones server-side con regeneración de ID, rate limiting, auditoría de dependencias |

## 2. Vista de contenedores

```mermaid
flowchart LR
    U([Usuario]) -->|HTTP :8080| FE[frontend<br/>nginx + React]
    FE -->|POST /graphql| GW[gateway<br/>FastAPI + Strawberry]
    GW <-->|sesiones + rate limit| R[(redis)]
    GW -->|LECTURAS: graphql.resolve<br/>rol wandersync_reader| SB[(Supabase Postgres<br/>pg_graphql)]
    GW -->|ESCRITURAS: createBooking| OR[orders<br/>orquestador SAGA + facturación]
    OR -->|POST/DELETE /reservations| FL[flights]
    OR -->|POST/DELETE /reservations| HO[hotels]
    OR -->|POST/DELETE /reservations| CA[cars]
    OR --> SB
    FL --> SB
    HO --> SB
    CA --> SB

    PS[prefect-server<br/>UI :4200] --- PR[prefect-runner<br/>flow + deployments]
    PR -->|DaskTaskRunner| DS[dask-scheduler<br/>dashboard :8787]
    DS --> DW1[dask-worker 1]
    DS --> DW2[dask-worker 2]
    DW1 -->|upsert por lotes| SB
    DW2 -->|upsert por lotes| SB
    DW1 -.->|scrape| EXT{{Booking.com / fuente sintética}}
    DW2 -.->|scrape| EXT
```

Servicios en `docker-compose.yml`: `frontend`, `gateway`, `flights`, `hotels`, `cars`, `orders`, `redis`,
`prefect-server`, `prefect-runner`, `dask-scheduler`, `dask-worker` (×2) y `migrate` (one-shot). El gateway **no publica
puerto**: solo nginx lo alcanza, de modo que `X-Real-IP` es confiable para el rate limiting.

## 3. API Gateway GraphQL

**Regla:** el frontend solo habla con `POST /graphql`.

### 3.1 Lecturas: cero over-fetching de extremo a extremo

Los resolvers de `flights`, `hotels`, `cars`, `searchPackages`, `order` y `myOrders` leen `info.selected_fields`
(Strawberry) y construyen una query hacia `graphql.resolve(...)` de **pg_graphql** pidiendo **solo las columnas
solicitadas**, con filtros, orden y paginación por cursor (`first/after`, `pageInfo`) resueltos en Postgres.

```
Cliente:  { searchPackages(...) { flights { edges { node { id price } } } } }
   ↓ (se omiten hoteles y autos: no fueron pedidos)
Postgres: query($ff,$fo,$fn,$fa){ flights: ws_catalog_flightsCollection(filter:$ff, orderBy:$fo, first:$fn, after:$fa)
          { edges { node { id price } } } }
```

- `searchPackages` consolida vuelos + hoteles + autos en **una sola** ida a la base de datos.
- Los valores del usuario viajan como **variables GraphQL**, nunca interpolados (sin inyección); los códigos IATA y fechas
  se validan antes.
- Verificado en `services/gateway/tests/test_gateway.py` (la query enviada contiene exactamente las columnas pedidas) y
  en los logs del gateway (`pg_graphql query: ...`).

### 3.2 Aislamiento de lectura

`pg_graphql` de Supabase solo expone el esquema `public`. Las tablas reales viven en el esquema `wandersync` (no expuesto,
con RLS activado) y se publican **únicamente** cinco vistas de solo lectura: `ws_catalog_flights`, `ws_catalog_hotels`,
`ws_catalog_cars`, `ws_order_summaries`, `ws_order_saga_steps`.

- Las consultas se ejecutan tras `SET LOCAL ROLE wandersync_reader`, un rol con `SELECT` solo sobre esas vistas.
- Se revocó todo a `anon`/`authenticated` (Supabase concede `ALL` por defecto en `public`).
- Para órdenes, el filtro `user_id = <sesión>` lo **inyecta el servidor**; el cliente no puede consultar órdenes ajenas.
- `users`, `payments` y `saga_log` no son alcanzables por GraphQL.

### 3.3 Escrituras y esquema

Mutaciones: `register`, `login`, `logout`, `createBooking` (resultados tipados con `union`: éxito | `ApiError`).
`createBooking` llama únicamente a `orders`: el gateway no conoce a vuelos, hoteles ni autos, así que el acoplamiento
queda oculto tras el orquestador. Protecciones: profundidad máx. 8, 15 alias, 2500 tokens, errores internos
enmascarados (los mensajes de validación deliberados se conservan), introspección desactivable
(`GRAPHQL_INTROSPECTION=false`).

## 4. Patrón SAGA (orquestación)

Elegí **orquestación** (no coreografía): el flujo `vuelo → hotel → auto → pago` es lineal, con un único dueño del
estado (`orders`), por lo que es más fácil de auditar, depurar y demostrar; el estado y cada paso quedan en `saga_log`.
No requiere un broker adicional (ahorra RAM). Costo asumido: `orders` es un punto de coordinación (mitigado con
persistencia + recuperación al reiniciar).

### 4.1 Camino feliz

```mermaid
sequenceDiagram
    autonumber
    actor C as Cliente
    participant GW as Gateway
    participant OR as Orders (SAGA)
    participant FL as Flights
    participant HO as Hotels
    participant CA as Cars
    participant DB as Supabase

    C->>GW: mutation createBooking
    GW->>OR: POST /orders (token interno)
    OR->>DB: INSERT orders (PENDING)
    OR-->>GW: 202 {orderId, PENDING}
    GW-->>C: BookingAccepted
    Note over OR: la saga corre en segundo plano
    OR->>FL: POST /reservations
    FL->>DB: seats-1 + reserva CONFIRMED
    FL-->>OR: 200 {amount}
    OR->>HO: POST /reservations
    HO-->>OR: 200 {amount}
    OR->>CA: POST /reservations
    CA-->>OR: 200 {amount}
    OR->>DB: INSERT payments (CAPTURED, suma)
    OR->>DB: orders → CONFIRMED (+ saga_log de cada paso)
    loop cada 1 s
        C->>GW: query order(id)
        GW->>DB: pg_graphql (vista, solo columnas pedidas)
    end
```

### 4.2 Fallo del auto → compensación automática

```mermaid
sequenceDiagram
    autonumber
    participant OR as Orders (SAGA)
    participant FL as Flights
    participant HO as Hotels
    participant CA as Cars
    participant DB as Supabase

    OR->>FL: POST /reservations
    FL-->>OR: 200 ✔
    OR->>HO: POST /reservations
    HO-->>OR: 200 ✔
    OR->>CA: POST /reservations
    CA--xOR: 503 ✘ (fallo simulado)
    OR->>DB: orders → COMPENSATING (motivo)
    Note over OR: compensa en orden inverso
    OR->>HO: DELETE /reservations/{order}
    HO-->>OR: 200 CANCELLED (rooms+1)
    OR->>FL: DELETE /reservations/{order}
    FL-->>OR: 200 CANCELLED (seats+1)
    OR->>DB: orders → CANCELLED
```

### 4.3 Máquina de estados de la orden

```mermaid
stateDiagram-v2
    [*] --> PENDING
    PENDING --> CONFIRMED: todos los pasos OK
    PENDING --> COMPENSATING: un paso falla
    COMPENSATING --> CANCELLED: todas las compensaciones OK
    COMPENSATING --> COMPENSATION_FAILED: reintentos agotados
    COMPENSATION_FAILED --> CANCELLED: reaper reintenta (cada 15 s)
    PENDING --> COMPENSATING: saga huérfana >2 min (reinicio del orquestador)
```

### 4.4 Garantías de diseño

| Riesgo | Mecanismo |
|---|---|
| Reintentos duplican efectos | Reservas **idempotentes** por `order_id` (`UNIQUE`); pedido idempotente por `(user_id, idempotency_key)` |
| Compensación sobre algo que nunca se hizo | `DELETE` tolerante: devuelve `NOT_FOUND` sin error |
| Timeout / 5xx: ¿se reservó o no? | Fallo **ambiguo** → se compensa también el paso que falló (idempotente) |
| Compensación falla | 5 reintentos con backoff exponencial y luego estado visible `COMPENSATION_FAILED` |
| Compensación agotada | **Reaper** cada 15 s reintenta la reversión de órdenes en `COMPENSATING`/`COMPENSATION_FAILED`, sin intervención manual |
| Caída del orquestador a mitad | El reaper detecta órdenes `PENDING` sin avance por más de 2 min que ninguna instancia está ejecutando (p. ej. tras un reinicio) y las revierte (aborto seguro) |
| Sobreventa | `UPDATE ... SET seats = seats - n WHERE seats >= n` atómico dentro de la transacción de la reserva |
| Pago | Último paso; si falla se compensan los tres anteriores. `refundPayment` reversa un pago capturado |

Escenarios simulables desde la UI/API (`simulateFailure`): `FLIGHT`, `HOTEL`, `CAR`, `PAYMENT`, `CAR_TIMEOUT` (fallo
ambiguo) y `CAR_FLAKY_COMPENSATION` (la compensación del hotel falla 2 veces para evidenciar los reintentos).

## 5. Ingesta distribuida: Dask + Prefect

```mermaid
flowchart TB
    subgraph Prefect Flow sync-travel-data
        P[particiones: 12 rutas de vuelo<br/>+ 8 ciudades × hoteles<br/>+ 8 ciudades × autos]
        P --> S[scrape-partition<br/>retries=3, backoff 5/15/45 s + jitter]
        S --> CL[clean-normalize]
        CL --> UP[upsert-supabase<br/>retries=3, por lotes]
    end
    S -.->|se ejecuta en| DW[(Dask workers)]
    CL -.-> DW
    UP -.-> DW
```

- El `DaskTaskRunner(address="tcp://dask-scheduler:8786")` envía cada task a los workers; la UI de Prefect muestra estado
  y reintentos y el dashboard de Dask (`:8787`) muestra la ejecución en paralelo. Una sola imagen (`ingestion/Dockerfile`)
  sirve a Prefect, al scheduler y a los workers, garantizando versiones idénticas.
- **Deployments:** `scheduled-sync` (cada 15 min) y `demo-with-retries` (cada scrape falla 2 veces antes de funcionar;
  ideal para la demo). Al arrancar se ejecuta una primera ingesta.
- **Reintentos:** política explícita en `scrape` y `upsert`. Además `mode=auto` cae a datos sintéticos únicamente tras
  agotar todos los reintentos contra la fuente real.
- **No pisar inventario:** el `UPSERT` actualiza precios/metadata pero **nunca** `seats_available`/`rooms_available`/
  `units_available`, para no resetear el stock que las reservas ya descontaron (verificado por test).
- **Cuello de botella de persistencia:** `executemany` por partición y conexión corta por task; el catálogo se lee por vistas.

### Fuente de datos (honestidad)

Las tres categorías usan **fuentes reales**, todas renderizadas con el `DynamicFetcher` de
[Scrapling](https://github.com/D4Vinci/Scrapling) (Chromium headless vía Playwright, helper `scrapers.render`):

| Datos | Fuente (`source`) | Qué se lee | Por partición |
|---|---|---|---|
| Hoteles | Booking.com (`booking.com`) | tarjetas `data-testid="property-card"` | 1 ciudad |
| Vuelos | Google Flights (`google_flights`) | el `aria-label` de cada resultado: precio, aerolínea, escalas, horas locales | 1 ruta × días `SCRAPE_FLIGHT_DAYS` (por defecto +3 y +7) |
| Vuelos | KAYAK (`kayak`) | texto visible de cada `Result item` (se descartan anuncios) | igual que Google Flights |
| Autos | KAYAK (`kayak`) | `alt` de las imágenes ("Vehicle type: Mini - Renault Kwid…", "Car agency: Alamo") y precio total ÷ días | 1 ciudad (aeropuerto) |

Por qué un navegador: un cliente HTTP simple (`httpx`, e incluso el `Fetcher` de Scrapling con huella TLS de Chrome)
recibe **HTTP 202** de Booking (reto anti-bot de AWS WAF que exige ejecutar JavaScript), y Google Flights/KAYAK
construyen los resultados con JavaScript. Detalles:

- Se parsea contenido semántico (aria-labels, textos alternativos, texto visible) y no las clases CSS ofuscadas, que
  cambian con cada despliegue de esos sitios.
- Las horas publicadas son locales de cada aeropuerto y se guardan como `timestamptz` (`destinations.AIRPORT_TZ`).
- Un solo navegador a la vez por worker (semáforo) para no exceder el `mem_limit` de 1 GB del contenedor.
- `disable_resources` se deja **apagado**: bloquear recursos impide que el reto anti-bot se resuelva (verificado).
- Estrellas de Booking: se leen del `aria-label` oficial ("Property rating: 4 out of 5"); cualquier valor fuera de 1-5
  se descarta (la BD lo exige con un `CHECK`).
- Vuelos: si una de las dos fuentes falla, basta con la otra. Si una fuente bloquea o cambia su markup, el task falla
  con `ScrapeBlockedError`, Prefect reintenta y en `mode=auto` el flow cae a la fuente sintética determinista
  (`source='mock'`) solo tras el último intento. Para forzar un modo: `SCRAPER_MODE=mock|real|auto`.
- Los sitios no publican cupos: los registros reales usan un inventario nominal (9 asientos, 5 habitaciones, 5 autos)
  que las reservas descuentan y el `UPSERT` no vuelve a pisar.

## 6. Ciberseguridad por diseño

Detalle y evidencias en [`seguridad/README.md`](seguridad/README.md).

| Requisito | Implementación | Evidencia |
|---|---|---|
| Session Fixation | Sesión server-side en Redis; `login`/`register` **crean un ID nuevo e invalidan el previo**; cookie firmada (HMAC), `HttpOnly`, `SameSite=Lax`, TTL deslizante | `e2e.py` y `test_gateway.py` |
| Hash de contraseñas | **Argon2id** (`m=19 MiB, t=3, p=1`), rehash automático si cambian parámetros, verificación de tiempo constante ante usuarios inexistentes, ejecutado en hilo | `test_argon2id_hash_and_verify` |
| Rate limiting | Ventana deslizante atómica (Lua/Redis): `login` 5/min por cuenta+IP y 20/min por IP; `register` 5/10 min; `createBooking` (checkout/pago) 5/min por usuario y 20/min por IP. Responde **HTTP 429 + `Retry-After`** | `e2e.py` (`[200×5, 429×3]`) |
| Supply chain | `pip-audit` (requirements con hashes, `--require-hashes`) por cada servicio + `npm audit`; **0 vulnerabilidades** | `docs/seguridad/pip-audit.txt`, `npm-audit.txt` |
| Otros | Contenedores no root, sin puertos internos publicados, token interno entre servicios, RLS + rol lector mínimo, CSP y cabeceras en nginx, límites de complejidad GraphQL, verificación de `Origin`, variables GraphQL (sin inyección) | |

## 7. Recursos

Medido con `docker stats` en reposo: el stack completo ocupa **≈ 1.0 GB** (gateway 81 MB, workers Dask 170 MB c/u,
Prefect server 214 MB, servicios de dominio ~41 MB c/u, redis 7 MB). Cada contenedor tiene `mem_limit` (suma de límites
≈ 3.6 GB). Prefect usa SQLite, Redis está acotado a 48 MB y las imágenes son `python:3.12-slim` de un solo proceso.
Docker Desktop con 4-6 GB es suficiente. Con el scraping real, cada worker
Dask abre un Chromium headless mientras procesa un hotel, por lo que su límite subió a 1 GB (suma de límites ≈ 4.3 GB) y la
imagen de ingesta incluye Chromium.

## 8. Decisiones y alternativas descartadas

| Decisión | Alternativa | Por qué |
|---|---|---|
| Orquestación SAGA | Coreografía con broker | Flujo lineal; auditabilidad; un contenedor menos |
| Lecturas por `pg_graphql` | Servicios REST + recorte en el gateway | Evita over-fetching real (BD→cliente) e integra la BD nativamente |
| Esquema `wandersync` + vistas `ws_*` | Tablas en `public` | La BD ya tenía datos de otra app (`users`, `orders`…); aislamiento total |
| Sesiones Redis | JWT sin estado | Permite regenerar/invalidar el ID (Session Fixation) de forma demostrable |
| Supabase cloud | Postgres/Hasura local | Requerido por el proyecto; se documenta la dependencia de red |
| Modo transacción del pooler (6543) | Modo sesión | Muchos servicios sin agotar el pool; `prepare_threshold=None` |
