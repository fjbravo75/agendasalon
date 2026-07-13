import React, { useDeferredValue, useEffect, useState } from "react";

import {
  barPercent,
  filterBusinesses,
  formatDateTime,
  formatShortDate,
  pluralize,
  sortBusinesses,
} from "./dashboard-utils.js";


const FILTERS = [
  { code: "all", label: "Todos" },
  { code: "operational", label: "Operativos" },
  { code: "setup_pending", label: "Por configurar" },
  { code: "pending_closure", label: "Con cierres pendientes" },
  { code: "inactive", label: "Pausados" },
];


async function fetchJson(url, signal) {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    signal,
  });
  if (response.redirected && response.url.includes("/cuenta/entrar/")) {
    throw new Error("Tu sesión ha caducado. Vuelve a entrar para continuar.");
  }
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : null;
  if (!response.ok) {
    throw new Error(payload?.error?.message || "No hemos podido consultar el estado de la plataforma.");
  }
  if (!payload) {
    throw new Error("El panel no ha devuelto una respuesta válida.");
  }
  return payload;
}


function useDashboardData(url) {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState({ data: null, error: null, loading: true });

  useEffect(() => {
    const controller = new AbortController();
    setState({ data: null, error: null, loading: true });
    fetchJson(url, controller.signal)
      .then((data) => setState({ data, error: null, loading: false }))
      .catch((error) => {
        if (error.name !== "AbortError") {
          setState((current) => ({ ...current, error, loading: false }));
        }
      });
    return () => controller.abort();
  }, [url, revision]);

  return { ...state, retry: () => setRevision((value) => value + 1) };
}


function MetricCard({ label, value, detail, tone = "default" }) {
  return (
    <article className={`superadmin-metric superadmin-metric--${tone}`}>
      <span>{label}</span>
      <strong>{value}</strong>
      <p>{detail}</p>
    </article>
  );
}


function AttentionSummary({ summary }) {
  if (!summary.businesses_setup_pending && !summary.pending_closure_appointments) {
    return (
      <section className="superadmin-clear-state" aria-label="Estado general">
        <span aria-hidden="true">✓</span>
        <div>
          <strong>Sin tareas de configuración pendientes</strong>
          <p>Los negocios activos tienen la base necesaria para trabajar.</p>
        </div>
      </section>
    );
  }
  return (
    <section className="superadmin-attention" aria-label="Asuntos que conviene supervisar">
      {summary.businesses_setup_pending ? (
        <article>
          <span>Configuración</span>
          <strong>{pluralize(summary.businesses_setup_pending, "negocio pendiente", "negocios pendientes")}</strong>
          <p>Faltan datos básicos antes de que esos equipos puedan trabajar con normalidad.</p>
        </article>
      ) : null}
      {summary.pending_closure_appointments ? (
        <article>
          <span>Seguimiento profesional</span>
          <strong>{pluralize(summary.pending_closure_appointments, "cita por cerrar", "citas por cerrar")}</strong>
          <p>El resultado lo registra cada equipo; aquí solo se muestra el seguimiento global.</p>
        </article>
      ) : null}
    </section>
  );
}


function ContinuitySummary({ continuity }) {
  return (
    <section
      className={`superadmin-continuity superadmin-continuity--${continuity.status.tone}`}
      aria-labelledby="superadmin-continuity-title"
    >
      <div className="superadmin-continuity__lead">
        <span>Continuidad del servicio</span>
        <h2 id="superadmin-continuity-title">{continuity.status.label}</h2>
        <p>{continuity.status.detail}</p>
        <a href={continuity.history_url}>Consultar historial y objetivos</a>
      </div>
      <dl className="superadmin-continuity__facts">
        <div>
          <dt>Última copia correcta</dt>
          <dd>{continuity.last_successful_at ? formatDateTime(continuity.last_successful_at) : "Sin ejecuciones registradas"}</dd>
        </div>
        <div>
          <dt>Integridad</dt>
          <dd>{continuity.integrity_label}</dd>
        </div>
        <div>
          <dt>Destino externo</dt>
          <dd>{continuity.external_destination.label}</dd>
        </div>
        <div>
          <dt>Automatización</dt>
          <dd>{continuity.schedule.label}</dd>
        </div>
      </dl>
    </section>
  );
}


function BusinessCard({ business, index }) {
  const setup = [
    ["Servicios", business.counts.services],
    ["Líneas", business.counts.work_lines],
    ["Horario", business.counts.schedule_rules],
    ["Accesos", business.counts.professionals],
    ["Privacidad", business.legal?.is_current ? 1 : 0],
  ];
  return (
    <article className="superadmin-business-card">
      <div className="superadmin-business-card__identity">
        <span className="superadmin-business-card__number">{String(index + 1).padStart(2, "0")}</span>
        <div>
          <div className="superadmin-business-card__title">
            <h3>{business.name}</h3>
            <span className={`superadmin-health superadmin-health--${business.health.tone}`}>
              {business.health.label}
            </span>
          </div>
          <p>{business.city} · {business.health.detail}</p>
          <small className="superadmin-business-card__legal">{business.legal?.label}</small>
        </div>
      </div>

      <div className="superadmin-business-card__setup" aria-label={`Configuración de ${business.name}`}>
        {setup.map(([label, value]) => (
          <span className={value ? "is-ready" : "is-missing"} key={label}>
            <small>{label}</small>
            <strong>{value}</strong>
          </span>
        ))}
      </div>

      <div className="superadmin-business-card__usage" aria-label={`Uso de ${business.name}`}>
        <span><strong>{business.counts.clients}</strong> clientes activos</span>
        <span><strong>{business.counts.appointments}</strong> citas registradas</span>
        <span><strong>{business.counts.upcoming_confirmed}</strong> próximas</span>
        <span className={business.counts.pending_closure ? "has-warning" : ""}>
          <strong>{business.counts.pending_closure}</strong> por cerrar
        </span>
      </div>

      <div className="superadmin-business-card__footer">
        <div className="superadmin-business-card__states">
          <span>{business.is_active ? "Acceso profesional activo" : "Acceso profesional pausado"}</span>
          <span>{business.is_active && business.public_booking_enabled ? "Reserva online activa" : "Reserva online pausada"}</span>
        </div>
        <div>
          <small>Último movimiento: {formatDateTime(business.last_activity_at)}</small>
          <a className="superadmin-business-card__manage" href={business.urls.detail}>Gestionar</a>
        </div>
      </div>
    </article>
  );
}


function Breakdown({ title, eyebrow, items }) {
  const maximum = Math.max(...items.map((item) => item.value), 0);
  return (
    <section className="superadmin-panel">
      <header className="superadmin-panel__head">
        <div><span>{eyebrow}</span><h2>{title}</h2></div>
      </header>
      <div className="superadmin-breakdown">
        {items.map((item) => (
          <div className="superadmin-breakdown__row" key={item.code}>
            <div><span>{item.label}</span><strong>{item.value}</strong></div>
            <span className="superadmin-breakdown__track" aria-hidden="true">
              <span style={{ width: `${barPercent(item.value, maximum)}%` }} />
            </span>
          </div>
        ))}
      </div>
    </section>
  );
}


function ActivityChart({ series }) {
  const maximum = Math.max(...series.map((item) => item.value), 1);
  const total = series.reduce((sum, item) => sum + item.value, 0);
  return (
    <section className="superadmin-panel superadmin-panel--activity">
      <header className="superadmin-panel__head">
        <div><span>Últimos 14 días</span><h2>Movimientos registrados</h2></div>
        <strong>{total}</strong>
      </header>
      <div className="superadmin-activity-chart" role="img" aria-label={`${total} movimientos registrados durante los últimos 14 días`}>
        {series.map((item, index) => (
          <div className="superadmin-activity-chart__day" key={item.date}>
            <span className="superadmin-activity-chart__bar">
              <span style={{ height: `${barPercent(item.value, maximum)}%` }} />
            </span>
            <strong>{item.value}</strong>
            <small>{index % 3 === 0 || index === series.length - 1 ? formatShortDate(item.date) : ""}</small>
          </div>
        ))}
      </div>
    </section>
  );
}


function RecentActivity({ events }) {
  return (
    <section className="superadmin-panel">
      <header className="superadmin-panel__head">
        <div><span>Hechos observables</span><h2>Actividad reciente</h2></div>
      </header>
      {events.length ? (
        <ol
          className={`superadmin-activity-list${events.length > 6 ? " superadmin-activity-list--scrollable" : ""}`}
          tabIndex={events.length > 6 ? 0 : undefined}
          aria-label={events.length > 6 ? `Actividad reciente, ${events.length} movimientos` : undefined}
        >
          {events.map((event) => (
            <li key={event.id}>
              <span className={`superadmin-activity-list__mark superadmin-activity-list__mark--${event.category}`} />
              <div>
                <strong>{event.business.name}</strong>
                <span>{event.event_label} · {event.origin_label}</span>
              </div>
              <time>{formatDateTime(event.created_at)}</time>
            </li>
          ))}
        </ol>
      ) : <p className="superadmin-empty-copy">Aún no hay movimientos registrados.</p>}
    </section>
  );
}


function DashboardLoading() {
  return (
    <div className="superadmin-loading" role="status">
      <span className="superadmin-loading-fallback__mark" aria-hidden="true" />
      <strong>Reuniendo el estado real de la plataforma…</strong>
    </div>
  );
}


export default function SuperadminDashboard({ config }) {
  const resource = useDashboardData(config.dataEndpoint);
  const [filter, setFilter] = useState("all");
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query);

  if (resource.loading) return <DashboardLoading />;
  if (resource.error) {
    return (
      <div className="superadmin-error" role="alert">
        <span>No se ha podido cargar el cuadro de mando.</span>
        <strong>{resource.error.message}</strong>
        <button className="button" type="button" onClick={resource.retry}>Volver a intentar</button>
      </div>
    );
  }

  const data = resource.data;
  const summary = data.summary;
  const visibleBusinesses = sortBusinesses(filterBusinesses(data.businesses, filter, deferredQuery));
  const filterCounts = {
    all: data.businesses.length,
    operational: summary.businesses_operational,
    setup_pending: summary.businesses_setup_pending,
    pending_closure: summary.businesses_with_pending_closure,
    inactive: summary.businesses_inactive,
  };

  return (
    <div className="superadmin-react-dashboard">
      <header className="superadmin-react-dashboard__hero">
        <div>
          <span className="superadmin-react-dashboard__eyebrow">Administración de la plataforma</span>
          <h1>El estado real de AgendaSalon.</h1>
          <p>Comprueba qué negocios están preparados, qué configuración falta y cómo se está utilizando la plataforma. La operativa diaria sigue en manos de cada equipo.</p>
        </div>
        <div className="superadmin-react-dashboard__actions">
          <button className="button button--secondary" type="button" onClick={resource.retry}>Actualizar datos</button>
          <a className="button button--secondary" href={config.businessListUrl}>Gestionar negocios</a>
          <a className="button" href={config.businessCreateUrl}>Nuevo negocio</a>
        </div>
      </header>

      <div className="superadmin-meta-line">
        <span>Actualizado {formatDateTime(data.generated_at)}</span>
        <span>{summary.professionals_active} accesos profesionales · {summary.clients_total} clientes · {summary.appointments_total} citas registradas</span>
      </div>

      <section className="superadmin-metrics" aria-label="Resumen general">
        <MetricCard label="Negocios operativos" value={summary.businesses_operational} detail={`De ${summary.businesses_active} negocios activos con la configuración básica completa.`} tone="primary" />
        <MetricCard label="Por configurar" value={summary.businesses_setup_pending} detail="Negocios activos con configuración operativa o documentación legal pendiente." tone={summary.businesses_setup_pending ? "warning" : "default"} />
        <MetricCard label="Equipos con citas por cerrar" value={summary.businesses_with_pending_closure} detail={`${summary.pending_closure_appointments} citas cuyo resultado debe registrar el profesional.`} tone={summary.businesses_with_pending_closure ? "warning" : "default"} />
        <MetricCard label="Reserva online activa" value={summary.businesses_public_booking} detail={`De ${summary.businesses_active} negocios activos que aceptan reservas online.`} />
      </section>

      <AttentionSummary summary={summary} />

      <ContinuitySummary continuity={data.continuity} />

      <div className="superadmin-dashboard-grid">
        <section className="superadmin-businesses" aria-labelledby="superadmin-businesses-title">
          <header className="superadmin-businesses__head">
            <div>
              <span>Resumen por negocio</span>
              <h2 id="superadmin-businesses-title">Negocios</h2>
              <p>Configuración, uso y tareas pendientes con la responsabilidad bien separada.</p>
            </div>
            <strong>{visibleBusinesses.length}</strong>
          </header>

          <div className="superadmin-business-controls">
            <label>
              <span className="sr-only">Buscar negocio</span>
              <input type="search" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Buscar por nombre o localidad" />
            </label>
            <div className="superadmin-filters" aria-label="Filtrar negocios">
              {FILTERS.map((item) => (
                <button className={filter === item.code ? "is-active" : ""} type="button" aria-pressed={filter === item.code} onClick={() => setFilter(item.code)} key={item.code}>
                  {item.label}<span>{filterCounts[item.code]}</span>
                </button>
              ))}
            </div>
          </div>

          <div className="superadmin-business-list">
            {visibleBusinesses.length ? visibleBusinesses.map((business, index) => (
              <BusinessCard business={business} index={index} key={business.id} />
            )) : (
              <div className="superadmin-empty-copy">
                <strong>No hay negocios que coincidan.</strong>
                <p>Prueba con otro nombre o cambia el filtro de estado.</p>
              </div>
            )}
          </div>
        </section>

        <aside className="superadmin-insights" aria-label="Actividad y distribución de citas">
          <ActivityChart series={data.activity_series} />
          <RecentActivity events={data.recent_activity} />
          <Breakdown title="Citas por estado" eyebrow="Resultado acumulado" items={data.appointment_statuses} />
          <Breakdown title="Citas por canal" eyebrow="Origen de la reserva" items={data.appointment_channels} />
        </aside>
      </div>
    </div>
  );
}
