# Handoff: estado del trabajo (2026-10-02, tarde)

`feat/scraping-real-y-ui` ya está mergeada en `master` (PR #1). Los cambios de esta sesión están **sin commitear** en `master`.

## Hecho en esta sesión (verificado)

- **Marca TravelSolutions** en el frontend (logo, `<title>`, footer). Nombres internos sin cambios.
- **Paquetes destacados** en la portada: BOG → CTG / MDE / MIA / MAD con `searchPackages(realOnly: true, first: 1)`
  para hoy+3 (una sola petición con alias). Total = vuelo + (hotel + auto) × 3 noches. Un clic llena el buscador y busca.
- **Datos en vivo**: sección con `dataSources` (registros, última actualización y precio mínimo por fuente) y enlaces a
  Prefect (:4200) y Dask (:8787). Etiqueta de fuente en cada vuelo, hotel y auto (`source` en `SEARCH_PACKAGES`).
- Casilla **«Solo datos reales»** en el buscador (activa por defecto; aviso si no hay vuelos reales para la fecha).
- KAYAK autos `< USD 8/día`: test ejecutado (9/9 de ingesta en la imagen del worker). Se borraron de la BD las 12 tarifas
  irreales que ya existían (Miami), ninguna referenciada por órdenes.
- **Bug SAGA corregido: reserva tardía tras compensación.** En `CAR_TIMEOUT` la reserva seguía viva en el participante y
  se completaba después de la compensación, dejando una reserva huérfana. Ahora la compensación deja una *tombstone*
  `CANCELLED` y la reserva tardía se rechaza (409). Aplicado en flights, hotels y cars. Se repararon 9 reservas de auto
  huérfanas de corridas anteriores del e2e. Nueva comprobación en `scripts/e2e.py`.
- **Bug corregido: `ReadError` intermitente** entre servicios (keep-alive de uvicorn de 5 s): los clientes httpx de
  orders y gateway expiran conexiones inactivas a los 2 s.
- e2e ahora elige los ítems con más inventario (los reales tienen pocas unidades y se agotaban tras varias corridas).
- Intervalo de sync por defecto: 30 min (la sync real tarda ~12).
- Resultados: e2e **23/23** (dos corridas seguidas), recuperación tras caída OK, gateway 13/13, SAGA 8/8, ingesta 9/9,
  `audit.sh` limpio. Docs actualizadas (arquitectura §3.4 y §4.4, demo, README).

## Pendiente

- Revisar la UI en un navegador: las herramientas de Chrome estaban desactivadas; solo se validaron build y consultas.
- Datos de prueba en Supabase (cuentas `e2e-…@example.com`, `rec-…`, `ui-…@test.local` y sus órdenes): limpiar antes de
  la demo si se quiere un historial limpio.
- Datos sintéticos viejos (≈650 vuelos, 64 hoteles, 64 autos `source='mock'`): siguen en la BD; la UI los oculta con
  «Solo datos reales». Borrarlos exige cuidar las órdenes que los referencian.
- Commitear y subir los cambios.
