import React, { useEffect, useMemo, useState } from "react";
import { useApolloClient, useLazyQuery, useMutation, useQuery } from "@apollo/client";
import { CREATE_BOOKING, DATA_SOURCES, FEATURED, LOGIN, LOGOUT, ME, MY_ORDERS, ORDER, REGISTER, SEARCH_PACKAGES } from "./queries.js";
import heroImg from "./assets/hero.jpg";
import flightImg from "./assets/flight.jpg";
import hotelImg from "./assets/hotel.jpg";
import carImg from "./assets/car.jpg";
import lakeImg from "./assets/lake.jpg";
import beachImg from "./assets/beach.jpg";
import coastImg from "./assets/coast.jpg";
import balloonsImg from "./assets/balloons.jpg";
import hikerImg from "./assets/hiker.jpg";
import mountainsImg from "./assets/mountains.jpg";

const CITIES = {
  BOG: "Bogotá", MDE: "Medellín", CTG: "Cartagena", CLO: "Cali",
  SMR: "Santa Marta", PEI: "Pereira", MIA: "Miami", MAD: "Madrid",
};
const FAILURES = [
  ["", "Ninguno (camino feliz)"],
  ["CAR", "Falla la reserva del auto"],
  ["HOTEL", "Falla la reserva del hotel"],
  ["FLIGHT", "Falla la reserva del vuelo"],
  ["PAYMENT", "Pago rechazado"],
  ["CAR_TIMEOUT", "Timeout del servicio de autos"],
  ["CAR_FLAKY_COMPENSATION", "Falla el auto + la compensación del hotel falla 2 veces (reintentos)"],
];
const TERMINAL = ["CONFIRMED", "CANCELLED", "COMPENSATION_FAILED"];
const STATUS_LABEL = {
  PENDING: "En proceso", CONFIRMED: "Confirmada", COMPENSATING: "Compensando…",
  CANCELLED: "Cancelada (compensada)", COMPENSATION_FAILED: "Compensación pendiente",
};
const STEP_LABEL = { FLIGHT: "Vuelo", HOTEL: "Hotel", CAR: "Auto", PAYMENT: "Pago" };
const DESTINATIONS = [
  { code: "CTG", img: coastImg, text: "Murallas, calles de colores y atardeceres sobre el Caribe." },
  { code: "SMR", img: beachImg, text: "Playas, Sierra Nevada y la puerta al Parque Tayrona." },
  { code: "MDE", img: hikerImg, text: "La ciudad de la eterna primavera, rodeada de montañas." },
];
const FEATURED_TRIPS = [
  { key: "ctg", code: "CTG", img: coastImg },
  { key: "mde", code: "MDE", img: hikerImg },
  { key: "mia", code: "MIA", img: beachImg },
  { key: "mad", code: "MAD", img: balloonsImg },
];
const FEATURED_NIGHTS = 3;
const SOURCE_LABEL = { "booking.com": "Booking.com", google_flights: "Google Flights", kayak: "KAYAK", mock: "Sintético" };
const KIND_LABEL = { flights: "Vuelos", hotels: "Hoteles", cars: "Autos" };
const money = (n) => (n == null ? "-" : `USD ${Number(n).toFixed(2)}`);
const isoDay = (offset) => new Date(Date.now() + offset * 864e5).toISOString().slice(0, 10);
const scrollTo = (id) => document.getElementById(id)?.scrollIntoView({ behavior: "smooth" });

function errorText(error) {
  const status = error?.networkError?.statusCode;
  if (status === 429) return "Demasiados intentos. Espera un momento e inténtalo de nuevo.";
  if (status === 403) return "Solicitud no permitida.";
  return error?.graphQLErrors?.[0]?.message || error?.message || "Error inesperado";
}

const Chip = ({ children, light }) => <span className={`chip ${light ? "light" : ""}`}><i />{children}</span>;
const Source = ({ source }) => source
  ? <span className={`src ${source === "mock" ? "fake" : ""}`}>{SOURCE_LABEL[source] ?? source}</span> : null;
const ago = (iso) => {
  if (!iso) return "-";
  const min = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (min < 1) return "hace instantes";
  if (min < 60) return `hace ${min} min`;
  const h = Math.round(min / 60);
  return h < 48 ? `hace ${h} h` : `hace ${Math.round(h / 24)} días`;
};

function Featured({ onPick }) {
  const date = isoDay(3); // los scrapers de vuelos reales cubren hoy+3 y hoy+7
  const { data, loading } = useQuery(FEATURED, { variables: { date, nights: FEATURED_NIGHTS } });
  const cards = FEATURED_TRIPS.map((t) => {
    const r = data?.[t.key];
    const f = r?.flights.edges[0]?.node, h = r?.hotels.edges[0]?.node, c = r?.cars.edges[0]?.node;
    const total = f && h && c ? f.price + (h.pricePerNight + c.pricePerDay) * FEATURED_NIGHTS : null;
    return { ...t, f, h, c, total };
  });
  return (
    <section className="section" id="paquetes">
      <div className="section-head">
        <div>
          <Chip>Paquetes destacados</Chip>
          <h2>Listos para <em>salir</em></h2>
          <p className="muted">Precios reales de hoy: vuelo + {FEATURED_NIGHTS} noches de hotel + auto, salida en 3 días desde Bogotá.</p>
        </div>
      </div>
      <div className="featured">
        {cards.map((t) => (
          <button key={t.key} className="feat" disabled={!t.total} onClick={() => onPick(t.code, date, FEATURED_NIGHTS)}>
            <div className="feat-img" style={{ backgroundImage: `url(${t.img})` }}><h3>{CITIES[t.code]}</h3></div>
            <div className="feat-body">
              {t.total ? (
                <>
                  <p className="feat-price"><em>${t.total.toFixed(0)}</em><small> USD</small></p>
                  <ul>
                    <li>{t.f.airline} <Source source={t.f.source} /></li>
                    <li>{t.h.name} <Source source={t.h.source} /></li>
                    <li>{t.c.model} <Source source={t.c.source} /></li>
                  </ul>
                </>
              ) : <p className="muted small">{loading ? "Calculando…" : "Sin datos reales para esta ruta todavía."}</p>}
            </div>
          </button>
        ))}
      </div>
    </section>
  );
}

function LiveData() {
  const { data } = useQuery(DATA_SOURCES, { pollInterval: 60000 });
  const rows = data?.dataSources ?? [];
  const host = window.location.hostname;
  const real = rows.filter((r) => r.source !== "mock").reduce((a, r) => a + r.items, 0);
  return (
    <section className="section" id="datos">
      <div className="section-head">
        <div>
          <Chip>Datos en vivo</Chip>
          <h2>Precios que <em>recolectamos</em></h2>
          <p className="muted">Workers de Dask extraen Booking.com, Google Flights y KAYAK; Prefect orquesta y reintenta cada tarea.
            {real > 0 && <> Hoy hay <b>{real.toLocaleString("es-CO")}</b> registros reales en el catálogo.</>}</p>
        </div>
        <div className="row-btns">
          <a className="btn dark" href={`http://${host}:4200`} target="_blank" rel="noreferrer">Panel de Prefect</a>
          <a className="btn light outline" href={`http://${host}:8787`} target="_blank" rel="noreferrer">Dashboard de Dask</a>
        </div>
      </div>
      <div className="sources">
        {rows.length === 0 && <p className="muted">Aún no hay datos ingeridos.</p>}
        {rows.map((r) => (
          <article key={r.id} className={`source-card ${r.source === "mock" ? "fake" : ""}`}>
            <span className="muted small">{KIND_LABEL[r.kind] ?? r.kind}</span>
            <h3>{SOURCE_LABEL[r.source] ?? r.source}</h3>
            <p className="count"><em>{r.items.toLocaleString("es-CO")}</em> registros</p>
            <p className="muted small">Desde {money(r.minPrice)} · actualizado {ago(r.lastFetchedAt)}</p>
          </article>
        ))}
      </div>
    </section>
  );
}

function Auth({ onDone, onClose }) {
  const [mode, setMode] = useState("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [msg, setMsg] = useState("");
  const [run, { loading }] = useMutation(mode === "login" ? LOGIN : REGISTER);

  async function submit(e) {
    e.preventDefault();
    setMsg("");
    try {
      const { data } = await run({ variables: { email, password } });
      const r = data.login ?? data.register;
      if (r.__typename === "ApiError") setMsg(r.message);
      else onDone();
    } catch (err) {
      setMsg(errorText(err));
    }
  }

  return (
    <div className="overlay" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form className="auth" onSubmit={submit}>
        <button type="button" className="close" onClick={onClose} aria-label="Cerrar">×</button>
        <Chip>{mode === "login" ? "Tu cuenta" : "Nueva cuenta"}</Chip>
        <h2>{mode === "login" ? <>Bienvenido de <em>vuelta</em></> : <>Empieza tu <em>viaje</em></>}</h2>
        <label>Correo<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
        <label>Contraseña (mín. 10 caracteres)
          <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={10} required />
        </label>
        <button className="btn dark" disabled={loading}>{mode === "login" ? "Entrar" : "Registrarme"}</button>
        {msg && <p className="error">{msg}</p>}
        <p className="muted center">
          <a href="#" onClick={(e) => { e.preventDefault(); setMode(mode === "login" ? "register" : "login"); }}>
            {mode === "login" ? "¿No tienes cuenta? Regístrate" : "¿Ya tienes cuenta? Inicia sesión"}
          </a>
        </p>
      </form>
    </div>
  );
}

function Timeline({ orderId }) {
  const { data } = useQuery(ORDER, {
    variables: { id: orderId },
    pollInterval: 1000,
    fetchPolicy: "network-only",
  });
  const order = data?.order;
  const client = useApolloClient();
  useEffect(() => {
    // Cuando la orden termina se deja de consultar (polling condicional).
    if (order && TERMINAL.includes(order.status)) {
      client.refetchQueries({ include: ["MisOrdenes"] });
    }
  }, [order?.status]);
  const done = order && TERMINAL.includes(order.status);
  return (
    <section className="journey" id="orden" style={{ backgroundImage: `url(${mountainsImg})` }}>
      <div className="journey-in">
        <Chip light>La saga</Chip>
        <h2>Tu reserva, <em>paso a paso</em></h2>
        {!order ? <p className="sub">Cargando orden…</p> : (
          <>
            <p className="sub">
              Orden <code>{order.id.slice(0, 8)}</code> · <span className={`badge ${order.status}`}>{STATUS_LABEL[order.status] ?? order.status}</span>
              {order.totalAmount != null && <> · Total <b>{money(order.totalAmount)}</b></>}
            </p>
            {order.failureReason && <p className="reason">Motivo: {order.failureReason}</p>}
            <ol className="steps">
              {order.steps.map((s, i) => (
                <li key={s.id} className={`${s.action} ${s.status}`}>
                  <span className="num">{i + 1}</span>
                  <span className="dot" />
                  <div>
                    <h4>{s.action === "EXECUTE" ? "Reservar" : "Compensar"} {STEP_LABEL[s.step] ?? s.step}</h4>
                    <p>{s.status}{s.detail && ` · ${s.detail}`}</p>
                  </div>
                </li>
              ))}
            </ol>
            {!done && <p className="sub live">Actualizando en vivo…</p>}
          </>
        )}
      </div>
    </section>
  );
}

function Pick({ title, img, items, value, onChange, render }) {
  return (
    <div className="pick-card">
      <div className="pick-head" style={{ backgroundImage: `url(${img})` }}><h3>{title}</h3></div>
      <div className="pick-body">
        {items.length === 0 && <p className="muted">Sin resultados</p>}
        {items.map(({ node }) => (
          <label key={node.id} className={`pick ${value === node.id ? "on" : ""}`}>
            <input type="radio" name={title} checked={value === node.id} onChange={() => onChange(node.id)} />
            {render(node)}
          </label>
        ))}
      </div>
    </div>
  );
}

function Booking({ user, onLogin }) {
  const [origin, setOrigin] = useState("BOG");
  const [destination, setDestination] = useState("MDE");
  const [date, setDate] = useState(isoDay(3));
  const [nights, setNights] = useState(3);
  const [realOnly, setRealOnly] = useState(true);
  const [pick, setPick] = useState({ flight: "", hotel: "", car: "" });
  const [failure, setFailure] = useState("");
  const [orderId, setOrderId] = useState(null);
  const [msg, setMsg] = useState("");
  const [search, { data, loading, error }] = useLazyQuery(SEARCH_PACKAGES);
  const [book, { loading: booking }] = useMutation(CREATE_BOOKING);
  const idempotencyKey = useMemo(() => crypto.randomUUID(), [pick.flight, pick.hotel, pick.car, failure, orderId]);
  const pkg = data?.searchPackages;
  const ready = pick.flight && pick.hotel && pick.car;
  const find = (list, id) => list?.edges.find((e) => e.node.id === id)?.node;
  const chosen = { flight: find(pkg?.flights, pick.flight), hotel: find(pkg?.hotels, pick.hotel), car: find(pkg?.cars, pick.car) };
  const n = Number(nights) || 0;
  const estimate = (Number(chosen.flight?.price) || 0) + (Number(chosen.hotel?.pricePerNight) || 0) * n + (Number(chosen.car?.pricePerDay) || 0) * n;

  useEffect(() => { if (pkg) scrollTo("resultados"); }, [pkg]);
  useEffect(() => { if (orderId) scrollTo("orden"); }, [orderId]);

  function runSearch(vars) {
    setPick({ flight: "", hotel: "", car: "" });
    setOrderId(null);
    setMsg("");
    search({ variables: { origin, destination, date, nights: Number(nights), realOnly, ...vars } });
  }

  function doSearch(e) {
    e.preventDefault();
    runSearch({});
  }

  function pickFeatured(code, day, nightsCount) {
    setOrigin("BOG"); setDestination(code); setDate(day); setNights(nightsCount); setRealOnly(true);
    runSearch({ origin: "BOG", destination: code, date: day, nights: nightsCount, realOnly: true });
  }

  async function reserve() {
    setMsg("");
    try {
      const { data: d } = await book({
        variables: {
          input: {
            flightId: pick.flight, hotelId: pick.hotel, carId: pick.car, nights: Number(nights),
            idempotencyKey, simulateFailure: failure || null,
          },
        },
      });
      const r = d.createBooking;
      if (r.__typename === "ApiError") setMsg(r.message);
      else setOrderId(r.orderId);
    } catch (err) {
      setMsg(errorText(err));
    }
  }

  const options = Object.entries(CITIES).map(([k, v]) => <option key={k} value={k}>{v} ({k})</option>);
  return (
    <>
      <form className="searchbar" id="buscar" onSubmit={doSearch}>
        <label>Origen<select value={origin} onChange={(e) => setOrigin(e.target.value)}>{options}</select></label>
        <label>Destino<select value={destination} onChange={(e) => setDestination(e.target.value)}>{options}</select></label>
        <label>Salida<input type="date" value={date} onChange={(e) => setDate(e.target.value)} required /></label>
        <label>Noches<input type="number" min="1" max="30" value={nights} onChange={(e) => setNights(e.target.value)} /></label>
        <button className="btn dark" disabled={loading}>{loading ? "Buscando…" : "Buscar paquetes"}</button>
        <label className="toggle" title="Excluye la fuente sintética de respaldo. Los vuelos reales cubren salidas en 3 y 7 días.">
          <input type="checkbox" checked={realOnly} onChange={(e) => setRealOnly(e.target.checked)} />
          Solo datos reales (Booking.com, Google Flights, KAYAK)
        </label>
      </form>
      {error && <p className="error center">{errorText(error)}</p>}

      <section className="intro">
        <img className="float f1" src={coastImg} alt="" />
        <img className="float f2" src={balloonsImg} alt="" />
        <img className="float f3" src={beachImg} alt="" />
        <img className="float f4" src={hikerImg} alt="" />
        <h2>Viajar es más que llegar a un destino — <em>es una sensación.</em></h2>
        <p>Vuelo, hotel y auto en una sola reserva. Si algo falla en el camino, deshacemos cada paso por ti
          y no queda nada cobrado ni reservado a medias.</p>
      </section>

      {!pkg && <Featured onPick={pickFeatured} />}

      {!pkg && (
        <section className="section" id="destinos">
          <div className="section-head">
            <div>
              <Chip>Destinos</Chip>
              <h2>Lugares que vas a <em>visitar</em></h2>
              <p className="muted">Elegidos a mano para tu próximo viaje.</p>
            </div>
            <button className="btn dark" onClick={() => scrollTo("buscar")}>Explorar todos</button>
          </div>
          <div className="places">
            {DESTINATIONS.map((d) => (
              <button key={d.code} className="place" style={{ backgroundImage: `url(${d.img})` }}
                onClick={() => { setDestination(d.code); scrollTo("buscar"); }}>
                <span><b>{CITIES[d.code]}</b>{d.text}</span>
              </button>
            ))}
          </div>
        </section>
      )}

      {pkg && (
        <>
          <section className="section" id="resultados">
            <div className="section-head">
              <div>
                <Chip>Resultados</Chip>
                <h2>Arma tu <em>paquete</em></h2>
                <p className="muted">{CITIES[origin]} → {CITIES[destination]} · {n} noches. Elige un vuelo, un hotel y un auto.</p>
                {realOnly && !pkg.flights.edges.length && <p className="muted small">No hay vuelos reales para esa fecha: los scrapers cubren salidas en 3 y 7 días. Desmarca «Solo datos reales» para ver la fuente sintética.</p>}
              </div>
            </div>
            <div className="grid3">
              <Pick title="Vuelos" img={flightImg} items={pkg.flights.edges} value={pick.flight} onChange={(v) => setPick({ ...pick, flight: v })}
                render={(f) => <span className="opt"><b>{f.airline}</b><small>{new Date(f.departureAt).toLocaleString("es-CO", { dateStyle: "short", timeStyle: "short" })} · {f.seatsAvailable} asientos <Source source={f.source} /></small><em>{money(f.price)}</em></span>} />
              <Pick title="Hoteles" img={hotelImg} items={pkg.hotels.edges} value={pick.hotel} onChange={(v) => setPick({ ...pick, hotel: v })}
                render={(h) => <span className="opt"><b>{h.name}</b><small>{"★".repeat(h.stars ?? 0)} <Source source={h.source} /></small><em>{money(h.pricePerNight)}<small>/noche</small></em></span>} />
              <Pick title="Autos" img={carImg} items={pkg.cars.edges} value={pick.car} onChange={(v) => setPick({ ...pick, car: v })}
                render={(c) => <span className="opt"><b>{c.model}</b><small>{c.provider} <Source source={c.source} /></small><em>{money(c.pricePerDay)}<small>/día</small></em></span>} />
            </div>
            <details className="gql"><summary>Consulta GraphQL enviada (solo los campos que la UI usa)</summary>
              <pre>{SEARCH_PACKAGES.loc.source.body.trim()}</pre>
            </details>
          </section>

          <section className="packages" style={{ backgroundImage: `url(${lakeImg})` }}>
            <Chip light>Tu paquete</Chip>
            <h2>Paquete <em>a tu medida</em></h2>
            <div className="pkg-card">
              <Chip>{ready ? "Listo para reservar" : "Elige las tres partes"}</Chip>
              <h3>{CITIES[origin]} → {CITIES[destination]}</h3>
              <p className="muted">{n + 1} días / {n} noches</p>
              <p className="price">{ready ? <>${estimate.toFixed(0)}</> : "—"}<small> USD estimado</small></p>
              <p className="incl">Incluye:</p>
              <ul className="checks">
                <li className={chosen.flight ? "ok" : ""}>{chosen.flight ? `Vuelo ${chosen.flight.airline}` : "Vuelo sin elegir"}</li>
                <li className={chosen.hotel ? "ok" : ""}>{chosen.hotel ? `${n} noches en ${chosen.hotel.name}` : "Hotel sin elegir"}</li>
                <li className={chosen.car ? "ok" : ""}>{chosen.car ? `${chosen.car.model} por ${n} días` : "Auto sin elegir"}</li>
              </ul>
              <label>Simular fallo (demo SAGA)
                <select value={failure} onChange={(e) => setFailure(e.target.value)}>
                  {FAILURES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
                </select>
              </label>
              {user
                ? <button className="btn dark wide" disabled={!ready || booking} onClick={reserve}>{booking ? "Reservando…" : "Reservar paquete"}</button>
                : <button className="btn dark wide" onClick={onLogin}>Inicia sesión para reservar</button>}
              {msg && <p className="error">{msg}</p>}
            </div>
          </section>
        </>
      )}
      {orderId && <Timeline orderId={orderId} />}
      <LiveData />
    </>
  );
}

function MyOrders() {
  const { data } = useQuery(MY_ORDERS, { fetchPolicy: "network-only" });
  const orders = data?.myOrders ?? [];
  return (
    <section className="section" id="viajes">
      <div className="section-head">
        <div>
          <Chip>Mis viajes</Chip>
          <h2>Tus órdenes <em>recientes</em></h2>
        </div>
      </div>
      {orders.length === 0 && <p className="muted">Aún no tienes órdenes.</p>}
      <div className="orders">
        {orders.map((o) => (
          <article key={o.id} className="order">
            <span className={`badge ${o.status}`}>{STATUS_LABEL[o.status] ?? o.status}</span>
            <p className="amount">{money(o.totalAmount)}</p>
            {o.failureReason && <p className="muted small">{o.failureReason}</p>}
            <footer><code>{o.id.slice(0, 8)}</code><small>{o.createdAt && new Date(o.createdAt).toLocaleString("es-CO", { dateStyle: "medium", timeStyle: "short" })}</small></footer>
          </article>
        ))}
      </div>
    </section>
  );
}

export default function App() {
  const { data, refetch } = useQuery(ME, { fetchPolicy: "network-only" });
  const client = useApolloClient();
  const [logout] = useMutation(LOGOUT);
  const [showAuth, setShowAuth] = useState(false);
  const user = data?.me;
  async function doLogout() {
    await logout();
    await client.clearStore();
    refetch();
  }
  return (
    <>
      <header className="hero" style={{ backgroundImage: `url(${heroImg})` }}>
        <nav>
          <a className="logo" href="#">TravelSolutions</a>
          <div className="links">
            <a href="#buscar">Buscar</a>
            <a href="#paquetes">Paquetes</a>
            <a href="#destinos">Destinos</a>
            <a href="#datos">Datos en vivo</a>
            {user && <a href="#viajes">Mis viajes</a>}
          </div>
          {user
            ? <div className="who"><span>{user.email}</span><button className="btn light" onClick={doLogout}>Salir</button></div>
            : <button className="btn light" onClick={() => setShowAuth(true)}>Iniciar sesión</button>}
        </nav>
        <div className="hero-copy">
          <Chip light>Vuelo + hotel + auto</Chip>
          <p>Arma tu viaje completo en una sola reserva: segura, consistente y sin sorpresas.</p>
          <button className="btn light" onClick={() => scrollTo("buscar")}>Reservar ahora</button>
        </div>
        <div className="giant">Descubre</div>
      </header>
      <main>
        <Booking user={user} onLogin={() => setShowAuth(true)} />
        {user && <MyOrders />}
      </main>
      <footer className="foot">
        <span className="logo">TravelSolutions</span>
        <span className="muted">Paquetes turísticos dinámicos · API Gateway GraphQL · SAGA</span>
      </footer>
      {showAuth && !user && <Auth onClose={() => setShowAuth(false)} onDone={() => { setShowAuth(false); refetch(); }} />}
    </>
  );
}
