import React, { useEffect, useMemo, useState } from "react";
import { useApolloClient, useLazyQuery, useMutation, useQuery } from "@apollo/client";
import { CREATE_BOOKING, LOGIN, LOGOUT, ME, MY_ORDERS, ORDER, REGISTER, SEARCH_PACKAGES } from "./queries.js";

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
const money = (n) => (n == null ? "-" : `USD ${Number(n).toFixed(2)}`);
const isoDay = (offset) => new Date(Date.now() + offset * 864e5).toISOString().slice(0, 10);

function errorText(error) {
  const status = error?.networkError?.statusCode;
  if (status === 429) return "Demasiados intentos. Espera un momento e inténtalo de nuevo.";
  if (status === 403) return "Solicitud no permitida.";
  return error?.graphQLErrors?.[0]?.message || error?.message || "Error inesperado";
}

function Auth({ onDone }) {
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
    <form className="card auth" onSubmit={submit}>
      <h2>{mode === "login" ? "Iniciar sesión" : "Crear cuenta"}</h2>
      <label>Correo<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} required /></label>
      <label>Contraseña (mín. 10 caracteres)
        <input type="password" value={password} onChange={(e) => setPassword(e.target.value)} minLength={10} required />
      </label>
      <button disabled={loading}>{mode === "login" ? "Entrar" : "Registrarme"}</button>
      {msg && <p className="error">{msg}</p>}
      <p className="muted">
        <a href="#" onClick={(e) => { e.preventDefault(); setMode(mode === "login" ? "register" : "login"); }}>
          {mode === "login" ? "¿No tienes cuenta? Regístrate" : "¿Ya tienes cuenta? Inicia sesión"}
        </a>
      </p>
    </form>
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
  if (!order) return <p className="muted">Cargando orden…</p>;
  return (
    <div className="card">
      <h3>Orden <code>{order.id.slice(0, 8)}</code></h3>
      <p className={`badge ${order.status}`}>{STATUS_LABEL[order.status] ?? order.status}</p>
      {order.failureReason && <p className="error">Motivo: {order.failureReason}</p>}
      {order.totalAmount != null && <p>Total: <b>{money(order.totalAmount)}</b></p>}
      <h4>Pasos de la SAGA</h4>
      <ol className="steps">
        {order.steps.map((s) => (
          <li key={s.id} className={`${s.action} ${s.status}`}>
            <span className="tag">{s.action === "EXECUTE" ? "Ejecutar" : "Compensar"}</span>
            <b>{s.step}</b> · {s.status}
            {s.detail && <em> ({s.detail})</em>}
          </li>
        ))}
      </ol>
      {!done && <p className="muted">Actualizando en vivo…</p>}
    </div>
  );
}

function Pick({ title, items, value, onChange, render }) {
  return (
    <div className="card">
      <h3>{title}</h3>
      {items.length === 0 && <p className="muted">Sin resultados</p>}
      {items.map(({ node }) => (
        <label key={node.id} className={`pick ${value === node.id ? "on" : ""}`}>
          <input type="radio" name={title} checked={value === node.id} onChange={() => onChange(node.id)} />
          {render(node)}
        </label>
      ))}
    </div>
  );
}

function Booking({ user }) {
  const [origin, setOrigin] = useState("BOG");
  const [destination, setDestination] = useState("MDE");
  const [date, setDate] = useState(isoDay(3));
  const [nights, setNights] = useState(3);
  const [pick, setPick] = useState({ flight: "", hotel: "", car: "" });
  const [failure, setFailure] = useState("");
  const [orderId, setOrderId] = useState(null);
  const [msg, setMsg] = useState("");
  const [search, { data, loading, error }] = useLazyQuery(SEARCH_PACKAGES);
  const [book, { loading: booking }] = useMutation(CREATE_BOOKING);
  const idempotencyKey = useMemo(() => crypto.randomUUID(), [pick.flight, pick.hotel, pick.car, failure, orderId]);
  const pkg = data?.searchPackages;
  const ready = pick.flight && pick.hotel && pick.car;

  function doSearch(e) {
    e.preventDefault();
    setPick({ flight: "", hotel: "", car: "" });
    setOrderId(null);
    setMsg("");
    search({ variables: { origin, destination, date, nights: Number(nights) } });
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
      <form className="card row" onSubmit={doSearch}>
        <label>Origen<select value={origin} onChange={(e) => setOrigin(e.target.value)}>{options}</select></label>
        <label>Destino<select value={destination} onChange={(e) => setDestination(e.target.value)}>{options}</select></label>
        <label>Salida<input type="date" value={date} onChange={(e) => setDate(e.target.value)} required /></label>
        <label>Noches<input type="number" min="1" max="30" value={nights} onChange={(e) => setNights(e.target.value)} /></label>
        <button disabled={loading}>Buscar paquetes</button>
      </form>
      {error && <p className="error">{errorText(error)}</p>}
      {pkg && (
        <>
          <div className="grid3">
            <Pick title="Vuelos" items={pkg.flights.edges} value={pick.flight} onChange={(v) => setPick({ ...pick, flight: v })}
              render={(f) => <span><b>{f.airline}</b> · {new Date(f.departureAt).toLocaleString("es-CO", { dateStyle: "short", timeStyle: "short" })} · {money(f.price)} <small>({f.seatsAvailable} asientos)</small></span>} />
            <Pick title="Hoteles" items={pkg.hotels.edges} value={pick.hotel} onChange={(v) => setPick({ ...pick, hotel: v })}
              render={(h) => <span><b>{h.name}</b> {"★".repeat(h.stars ?? 0)} · {money(h.pricePerNight)}/noche</span>} />
            <Pick title="Autos" items={pkg.cars.edges} value={pick.car} onChange={(v) => setPick({ ...pick, car: v })}
              render={(c) => <span><b>{c.model}</b> · {c.provider} · {money(c.pricePerDay)}/día</span>} />
          </div>
          <details className="card"><summary>Consulta GraphQL enviada (solo los campos que la UI usa)</summary>
            <pre>{SEARCH_PACKAGES.loc.source.body.trim()}</pre>
          </details>
          <div className="card row">
            <label className="grow">Simular fallo (demo SAGA)
              <select value={failure} onChange={(e) => setFailure(e.target.value)}>
                {FAILURES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}
              </select>
            </label>
            <button disabled={!ready || booking || !user} onClick={reserve}>Reservar paquete</button>
          </div>
          {!user && <p className="error">Inicia sesión para reservar.</p>}
          {msg && <p className="error">{msg}</p>}
        </>
      )}
      {orderId && <Timeline orderId={orderId} />}
    </>
  );
}

function MyOrders() {
  const { data } = useQuery(MY_ORDERS, { fetchPolicy: "network-only" });
  const orders = data?.myOrders ?? [];
  return (
    <div className="card">
      <h3>Mis órdenes recientes</h3>
      {orders.length === 0 && <p className="muted">Aún no tienes órdenes.</p>}
      <ul className="orders">
        {orders.map((o) => (
          <li key={o.id}>
            <code>{o.id.slice(0, 8)}</code> <span className={`badge ${o.status}`}>{STATUS_LABEL[o.status] ?? o.status}</span>
            {" "}{money(o.totalAmount)} {o.failureReason && <small className="muted">· {o.failureReason}</small>}
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function App() {
  const { data, refetch } = useQuery(ME, { fetchPolicy: "network-only" });
  const client = useApolloClient();
  const [logout] = useMutation(LOGOUT);
  const user = data?.me;
  async function doLogout() {
    await logout();
    await client.clearStore();
    refetch();
  }
  return (
    <div className="wrap">
      <header>
        <h1>WanderSync <span>Travel</span></h1>
        {user && <div><span className="muted">{user.email}</span> <button className="ghost" onClick={doLogout}>Salir</button></div>}
      </header>
      {!user ? <Auth onDone={() => refetch()} /> : null}
      <Booking user={user} />
      {user && <MyOrders />}
    </div>
  );
}
