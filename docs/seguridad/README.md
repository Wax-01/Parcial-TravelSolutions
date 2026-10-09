# Ciberseguridad y resiliencia: evidencias

## 1. Gestión segura de identidad y sesiones

- **Argon2id** (`argon2-cffi`): `m=19456 KiB, t=3, p=1` (recomendación OWASP). El hash queda en formato
  `$argon2id$v=19$m=19456,t=3,p=1$...`. Si se suben los parámetros, el siguiente login re-hashea automáticamente.
  Para emails inexistentes se verifica contra un hash señuelo (evita enumeración por tiempo). El cálculo corre en un hilo
  para no bloquear el event loop.
- **Session Fixation:** la sesión vive en Redis (`sess:<id>`); la cookie es `id.hmac`, `HttpOnly`, `SameSite=Lax`, con TTL
  deslizante de 30 min (`Secure` con `COOKIE_SECURE=true` tras HTTPS). `login` y `register` **generan un ID nuevo y
  borran el anterior**. Un atacante que fijó un ID previo queda como anónimo.
  - Prueba unitaria: `test_session_id_is_regenerated_on_login_and_old_id_is_invalid`.
  - Prueba e2e (`docs/evidencias/e2e.txt`): `session id REGENERATED on login` y `pre-login session id no longer authenticates`.
  - Cookies falsificadas o alteradas se rechazan sin consultar Redis (`test_forged_or_tampered_cookie_is_rejected`).
- `logout` destruye la sesión en el servidor.

## 2. Protección de superficie

Rate limiting con ventana deslizante atómica (script Lua en Redis), aplicado a nivel HTTP antes de ejecutar la operación:

| Operación | Regla | Respuesta |
|---|---|---|
| `login` | 5/min por IP+cuenta y 20/min por IP | HTTP 429 + `Retry-After` |
| `register` | 5 / 10 min por IP | HTTP 429 |
| `createBooking` (checkout + pago) | 5/min por usuario y 20/min por IP | HTTP 429 |

Evidencia: e2e `rate limit on login -> HTTP 429 ... [200, 200, 200, 200, 200, 429, 429, 429]`.

Otras medidas: verificación de `Origin` en POST, `Content-Type` JSON (sin queries por GET), límites de profundidad (8),
alias (15) y tokens (2500) en GraphQL, errores internos enmascarados, variables GraphQL en vez de interpolación,
cabeceras de seguridad y CSP en nginx, contenedores sin root, token interno (derivado de `JWT_SECRET`) entre servicios y
`.env` fuera de git.

## 3. Aislamiento de datos

Tablas en el esquema `wandersync` (no expuesto) con **RLS activado**; lectura GraphQL solo por vistas `ws_*` y el rol
`wandersync_reader` (`SELECT` únicamente en esas vistas); `REVOKE ALL` sobre las vistas a `anon`/`authenticated`
(Supabase concede `ALL` por defecto en `public`). El filtro por `user_id` de las órdenes lo pone el servidor.

## 4. Supply chain

- Todas las dependencias Python están **fijadas con hashes** (`pip-compile --generate-hashes`) y se instalan con
  `pip install --require-hashes`; el frontend usa `package-lock.json` y `npm ci`.
- `scripts/audit.sh` (`make audit`) ejecuta `pip-audit` sobre los 7 `requirements.txt` (gateway, orders, flights, hotels,
  cars, ingestion, db) y `npm audit` del frontend.

| Componente | Herramienta | Resultado (2026-10-09) |
|---|---|---|
| 7 componentes Python | pip-audit 2.10.1 | **No known vulnerabilities found** (7/7) |
| Frontend | npm audit | **found 0 vulnerabilities** |

Hallazgo corregido durante el desarrollo: `npm audit` reportó 2 vulnerabilidades (moderada y alta) en `vite`/`esbuild`
(servidor de desarrollo); se resolvieron actualizando a `vite ^8.3` y `@vitejs/plugin-react ^6.1`.
En la auditoría del 2026-10-09 apareció un aviso nuevo, `source-map-js` 1.2.1 (alta, GHSA-68fv-2mgg-jv7q: DoS del
event loop; dependencia de compilación de PostCSS/Vite), corregido con `npm audit fix` (→ 1.2.2). La misma auditoría
cubre el `prefect-client` que se añadió a `orders` para orquestar la SAGA con Prefect: sin vulnerabilidades conocidas.

Informes crudos: [`pip-audit.txt`](pip-audit.txt), [`npm-audit.txt`](npm-audit.txt).

## 5. Limitaciones de seguridad conocidas

- `ALLOW_FAILURE_SIMULATION`, `GRAPHQL_INTROSPECTION` y `ENABLE_DEBUG_ENDPOINTS` están activos en el compose por ser un
  entorno de demostración; en producción deben desactivarse y `COOKIE_SECURE=true` con TLS.
- El tráfico entre contenedores es HTTP dentro de la red de Docker (sin mTLS).
- Registro: los correos duplicados devuelven un mensaje genérico para reducir enumeración de cuentas.
