import React, { useEffect, useRef, useState } from "react";

import {
  appointmentDetailUrl,
  buildAppointmentAssistantUrl,
  buildMonthCells,
  capitalizeFirst,
  dateParts,
  formatClock,
  formatDate,
  getTimelineRange,
  hourLabels,
  markerPosition,
  monthFromDate,
  sameMonth,
  shiftMonth,
  timelineHeight,
  timelinePosition,
} from "./agenda-utils.js";


const WEEKDAYS = ["L", "M", "X", "J", "V", "S", "D"];
const ROW_HEIGHT = 24;

const STATUS_COPY = {
  available: ["Con huecos", "Hay opciones completas para esta duración."],
  past: ["Historial", "Consulta las citas registradas en esta jornada."],
  closed: ["Jornada cerrada", "No se ofrecen huecos nuevos para este día."],
  unavailable: ["Sin hueco", "No cabe una cita completa con esta duración."],
};

const REASON_COPY = {
  dia_pasado: "El día ya ha pasado.",
  negocio_inactivo: "El negocio está pausado.",
  sin_lineas_activas: "No hay líneas activas.",
  festivo_nacional: "La jornada está cerrada por festivo nacional.",
  sin_horario: "No hay horario activo para este día.",
  fuera_de_horario: "El horario de hoy ya ha terminado.",
  cierre_negocio: "Hay un cierre completo registrado.",
  sin_hueco: "No queda un bloque completo para esta duración.",
};

const SLOT_REASON_COPY = {
  rellena_hueco_exacto: "Encaja sin dejar huecos sueltos",
  compacta_agenda: "Ayuda a concentrar la jornada",
  evita_restos_pequenos: "Evita un margen difícil de aprovechar",
  hueco_valido: "Disponible para ofrecer",
};


async function fetchJson(url, signal) {
  const response = await fetch(url, {
    credentials: "same-origin",
    headers: { Accept: "application/json" },
    signal,
  });
  if (response.redirected && response.url.includes("/entrar/")) {
    throw new Error("Tu sesión ha caducado. Vuelve a entrar para continuar.");
  }
  const contentType = response.headers.get("content-type") || "";
  const payload = contentType.includes("application/json")
    ? await response.json()
    : null;
  if (!response.ok) {
    throw new Error(
      payload?.error?.message || "No hemos podido consultar la agenda.",
    );
  }
  if (!payload) {
    throw new Error("La agenda no ha devuelto una respuesta válida.");
  }
  return payload;
}


function useJson(url) {
  const [revision, setRevision] = useState(0);
  const [state, setState] = useState({
    data: null,
    error: null,
    loading: true,
  });

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

  return {
    ...state,
    retry: () => setRevision((current) => current + 1),
  };
}


export default function ProfessionalAgenda({ config }) {
  const [selectedDate, setSelectedDate] = useState(config.initialDate);
  const [duration, setDuration] = useState(config.initialDuration);
  const [viewMonth, setViewMonth] = useState(monthFromDate(config.initialDate));
  const [selectedSlot, setSelectedSlot] = useState(null);
  const [activeLineId, setActiveLineId] = useState(null);
  const [decisionFocusRequest, setDecisionFocusRequest] = useState(0);

  const dayUrl = `${config.dayEndpoint}?${new URLSearchParams({
    date: selectedDate,
    duration: String(duration),
  })}`;
  const monthUrl = `${config.monthEndpoint}?${new URLSearchParams({
    year: String(viewMonth.year),
    month: String(viewMonth.month),
    duration: String(duration),
  })}`;
  const dayResource = useJson(dayUrl);
  const monthResource = useJson(monthUrl);
  const dayData = dayResource.data;
  const businessName = dayData?.business?.name || config.businessName;

  function chooseDate(dateValue, nextSlot = null) {
    setSelectedDate(dateValue);
    setSelectedSlot(nextSlot);
    const nextMonth = monthFromDate(dateValue);
    if (!sameMonth(viewMonth, nextMonth)) {
      setViewMonth(nextMonth);
    }
  }

  function chooseDuration(event) {
    setDuration(Number(event.target.value));
    setSelectedSlot(null);
  }

  function chooseSlot(slot) {
    setSelectedSlot(slot);
    setActiveLineId(slot.work_line_id);
  }

  function chooseDecisionSlot(slot) {
    chooseSlot(slot);
    setDecisionFocusRequest((current) => current + 1);
  }

  function chooseSuggestion(slot) {
    chooseDate(slot.starts_at.slice(0, 10), slot);
    setActiveLineId(slot.work_line_id);
    setDecisionFocusRequest((current) => current + 1);
  }

  const newAppointmentUrl = buildAppointmentAssistantUrl(
    config.appointmentAssistantUrl,
    selectedSlot,
    selectedDate,
  );

  return (
    <div className="react-agenda" aria-busy={dayResource.loading}>
      <header className="react-agenda__hero">
        <div>
          <span className="react-agenda__eyebrow">Agenda profesional</span>
          <h1>La jornada, a una sola mirada.</h1>
          <p>
            {businessName}. Revisa el día por líneas, encuentra un hueco completo
            y pasa a preparar la cita cuando tengas la decisión clara.
          </p>
        </div>
        <div className="react-agenda__hero-actions">
          <a className="button button--secondary" href={config.professionalSummaryUrl}>
            Volver al resumen
          </a>
          <a className="button" href={newAppointmentUrl}>
            Nueva cita
          </a>
        </div>
      </header>

      <div className="react-agenda__controls" aria-label="Controles de agenda">
        <label className="agenda-duration-control">
          <span>Duración que necesitas</span>
          <select value={duration} onChange={chooseDuration}>
            {config.durationOptions.map((value) => (
              <option key={value} value={value}>
                {value} minutos
              </option>
            ))}
          </select>
        </label>
        <div className="agenda-selected-day" aria-live="polite">
          <span>Día seleccionado</span>
          <strong>
            {formatDate(selectedDate, {
              weekday: "long",
              day: "numeric",
              month: "long",
            })}
          </strong>
        </div>
      </div>

      <div className="professional-agenda">
        <aside className="agenda-sidebar" aria-label="Calendario y opciones">
          <MonthCalendar
            resource={monthResource}
            selectedDate={selectedDate}
            viewMonth={viewMonth}
            onChangeMonth={setViewMonth}
            onChooseDate={chooseDate}
          />
          <AgendaDecision
            dayData={dayData}
            duration={duration}
            selectedDate={selectedDate}
            selectedSlot={selectedSlot}
            focusContinueRequest={decisionFocusRequest}
            onChooseSlot={chooseDecisionSlot}
            onChooseSuggestion={chooseSuggestion}
            appointmentAssistantUrl={config.appointmentAssistantUrl}
          />
        </aside>

        <section className="agenda-day-panel">
          {dayResource.error ? (
            <ResourceError
              message={dayResource.error.message}
              onRetry={dayResource.retry}
            />
          ) : null}
          {!dayResource.error && !dayData ? <AgendaSkeleton /> : null}
          {dayData ? (
            <DayView
              config={config}
              data={dayData}
              duration={duration}
              selectedDate={selectedDate}
              selectedSlot={selectedSlot}
              activeLineId={activeLineId}
              onChooseLine={setActiveLineId}
              onChooseSlot={chooseSlot}
            />
          ) : null}
        </section>
      </div>
    </div>
  );
}


function MonthCalendar({
  resource,
  selectedDate,
  viewMonth,
  onChangeMonth,
  onChooseDate,
}) {
  const monthLabel = capitalizeFirst(new Intl.DateTimeFormat("es-ES", {
    month: "long",
    year: "numeric",
  }).format(new Date(viewMonth.year, viewMonth.month - 1, 1, 12)));
  const cells = buildMonthCells(resource.data?.days || []);

  return (
    <section className="agenda-card agenda-calendar" aria-labelledby="calendar-title">
      <header className="agenda-calendar__head">
        <div>
          <span>Disponibilidad del mes</span>
          <h2 id="calendar-title">{monthLabel}</h2>
        </div>
        <div className="agenda-calendar__navigation">
          <button
            type="button"
            aria-label="Mes anterior"
            onClick={() => onChangeMonth(shiftMonth(viewMonth, -1))}
          >
            ←
          </button>
          <button
            type="button"
            aria-label="Mes siguiente"
            onClick={() => onChangeMonth(shiftMonth(viewMonth, 1))}
          >
            →
          </button>
        </div>
      </header>

      {resource.error ? (
        <ResourceError message={resource.error.message} onRetry={resource.retry} compact />
      ) : null}
      {!resource.error && !resource.data ? (
        <div className="agenda-calendar__loading" role="status">
          Cargando el mes…
        </div>
      ) : null}
      {resource.data ? (
        <>
          <div className="agenda-calendar__weekdays" aria-hidden="true">
            {WEEKDAYS.map((weekday) => <span key={weekday}>{weekday}</span>)}
          </div>
          <div className="agenda-calendar__grid">
            {cells.map((cell) => (
              cell.empty ? (
                <span key={cell.key} className="agenda-calendar__empty" />
              ) : (
                <button
                  key={cell.key}
                  type="button"
                  className={`agenda-calendar__day is-${cell.status}${
                    cell.date === selectedDate ? " is-selected" : ""
                  }`}
                  aria-pressed={cell.date === selectedDate}
                  aria-label={calendarDayLabel(cell)}
                  onClick={() => onChooseDate(cell.date)}
                >
                  <strong>{dateParts(cell.date).day}</strong>
                  {cell.first_slot ? <small>{formatClock(cell.first_slot.starts_at)}</small> : <small>&nbsp;</small>}
                </button>
              )
            ))}
          </div>
          <div className="agenda-calendar__legend" aria-label="Leyenda del calendario">
            <span><i className="is-available" />Con hueco</span>
            <span><i className="is-selected" />Seleccionado</span>
            <span><i className="is-muted" />Sin hueco o pasado</span>
          </div>
        </>
      ) : null}
    </section>
  );
}


function calendarDayLabel(day) {
  const dateLabel = formatDate(day.date, {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
  if (day.first_slot) {
    return `${dateLabel}. Primer hueco a las ${formatClock(day.first_slot.starts_at)}.`;
  }
  return `${dateLabel}. Sin hueco para esta duración.`;
}


function AgendaDecision({
  dayData,
  duration,
  selectedDate,
  selectedSlot,
  focusContinueRequest,
  onChooseSlot,
  onChooseSuggestion,
  appointmentAssistantUrl,
}) {
  const continueLinkRef = useRef(null);
  const recommended = dayData?.recommended_slot;
  const previewSlot = selectedSlot || recommended;
  const continueUrl = buildAppointmentAssistantUrl(
    appointmentAssistantUrl,
    previewSlot,
    selectedDate,
  );

  useEffect(() => {
    if (focusContinueRequest > 0) {
      continueLinkRef.current?.focus();
    }
  }, [focusContinueRequest]);

  return (
    <section className="agenda-card agenda-decision" aria-labelledby="decision-title">
      <span className="agenda-card__eyebrow">Siguiente decisión</span>
      <h2 id="decision-title">
        {selectedSlot ? "Hora elegida" : recommended ? "Mejor encaje del día" : "Busca el próximo hueco"}
      </h2>

      {previewSlot ? (
        <div className="agenda-decision__slot">
          <strong>{formatClock(previewSlot.starts_at)}</strong>
          <span>{previewSlot.work_line_name} · {duration} min</span>
          <small>{SLOT_REASON_COPY[previewSlot.reason] || "Disponible para ofrecer"}</small>
          {!selectedSlot && recommended ? (
            <button type="button" onClick={() => onChooseSlot(recommended)}>
              Elegir esta hora
            </button>
          ) : null}
          <a ref={continueLinkRef} className="button button--wide" href={continueUrl}>
            Preparar esta cita
          </a>
        </div>
      ) : (
        <p className="agenda-decision__empty">
          Selecciona otro día disponible o usa una de las próximas opciones.
        </p>
      )}

      {!recommended && dayData?.suggestions?.length ? (
        <div className="agenda-suggestions">
          <h3>Próximas opciones</h3>
          {dayData.suggestions.map((slot) => (
            <button
              key={`${slot.work_line_id}-${slot.starts_at}`}
              type="button"
              onClick={() => onChooseSuggestion(slot)}
            >
              <strong>
                {formatDate(slot.starts_at.slice(0, 10), { weekday: "short", day: "numeric" })}
                {" · "}{formatClock(slot.starts_at)}
              </strong>
              <span>{slot.work_line_name}</span>
            </button>
          ))}
        </div>
      ) : null}
    </section>
  );
}


function DayView({
  config,
  data,
  duration,
  selectedDate,
  selectedSlot,
  activeLineId,
  onChooseLine,
  onChooseSlot,
}) {
  const lines = data.work_lines;
  const mobileLineId = lines.some((line) => line.id === activeLineId)
    ? activeLineId
    : lines[0]?.id;
  const appointmentCount = lines.reduce(
    (total, line) => total + line.appointments.length,
    0,
  );
  const availableCount = lines.reduce(
    (total, line) => total + line.available_slots.length,
    0,
  );
  const [statusLabel, statusText] = STATUS_COPY[data.calendar.status] || STATUS_COPY.unavailable;
  const reasonText = REASON_COPY[data.calendar.reason] || statusText;

  return (
    <>
      <header className="agenda-day-panel__head">
        <div>
          <span>{statusLabel}</span>
          <h2>
            {capitalizeFirst(formatDate(selectedDate, {
              weekday: "long",
              day: "numeric",
              month: "long",
            }))}
          </h2>
          <p>{reasonText}</p>
        </div>
        <div className="agenda-day-panel__facts" aria-label="Resumen del día">
          <span><strong>{appointmentCount}</strong> {appointmentCount === 1 ? "cita" : "citas"}</span>
          <span><strong>{availableCount}</strong> inicios válidos</span>
          <span><strong>{duration}</strong> minutos</span>
        </div>
      </header>

      {data.holidays.length ? (
        <div className="agenda-day-notice agenda-day-notice--closed">
          <strong>{data.holidays[0].name}</strong>
          <span>Festivo nacional aplicado a esta jornada.</span>
        </div>
      ) : null}

      {lines.length ? (
        <>
          <div className="agenda-line-tabs" aria-label="Líneas de trabajo">
            {lines.map((line) => (
              <button
                key={line.id}
                type="button"
                className={line.id === mobileLineId ? "is-active" : ""}
                aria-pressed={line.id === mobileLineId}
                onClick={() => onChooseLine(line.id)}
              >
                {line.name}
              </button>
            ))}
          </div>
          <Timeline
            config={config}
            data={data}
            selectedDate={selectedDate}
            selectedSlot={selectedSlot}
            mobileLineId={mobileLineId}
            onChooseSlot={onChooseSlot}
          />
        </>
      ) : (
        <div className="agenda-empty-state">
          <strong>No hay líneas activas para esta jornada.</strong>
          <p>Revisa la capacidad del negocio antes de intentar crear una cita.</p>
          <a className="button" href={config.scheduleUrl}>Revisar horarios</a>
        </div>
      )}
    </>
  );
}


function Timeline({
  config,
  data,
  selectedDate,
  selectedSlot,
  mobileLineId,
  onChooseSlot,
}) {
  const range = getTimelineRange(data);
  const height = timelineHeight(range, ROW_HEIGHT);
  const labels = hourLabels(range);
  const currentMarker = data.calendar.status === "closed"
    ? null
    : data.calendar.calculated_from;
  const pastDay = selectedDate < config.initialDate;

  return (
    <section className="agenda-timeline-card" aria-label="Jornada por líneas de trabajo">
      <div className="agenda-timeline-card__hint">
        <span>Las horas en verde indican dónde cabe la cita completa.</span>
        <strong>Tramos de {data.calendar.slot_interval_minutes} min</strong>
      </div>
      <div
        className="agenda-timeline__scroll"
        tabIndex="0"
        aria-label="Desplazar la jornada por líneas de trabajo"
      >
        <div
          className="agenda-timeline"
          style={{
            "--line-count": data.work_lines.length,
            "--timeline-height": `${height}px`,
          }}
        >
          <div className="agenda-timeline__corner">Hora</div>
          {data.work_lines.map((line) => (
            <div
              key={`head-${line.id}`}
              className={`agenda-timeline__line-head${line.id === mobileLineId ? " is-mobile-active" : ""}`}
            >
              <strong>{line.name}</strong>
              <span>{line.appointments.length ? `${line.appointments.length} cita${line.appointments.length === 1 ? "" : "s"}` : "Sin citas"}</span>
            </div>
          ))}

          <div className="agenda-timeline__rail" style={{ height }}>
            {labels.map((item) => (
              <span
                key={item.minutes}
                style={{ top: `${((item.minutes - range.start) / 15) * ROW_HEIGHT}px` }}
              >
                {item.label}
              </span>
            ))}
          </div>

          {data.work_lines.map((line, index) => (
            <TimelineLine
              key={line.id}
              config={config}
              data={data}
              line={line}
              range={range}
              height={height}
              currentMarker={currentMarker}
              pastDay={pastDay}
              selectedSlot={selectedSlot}
              mobileActive={line.id === mobileLineId}
              showNowLabel={index === 0}
              onChooseSlot={onChooseSlot}
            />
          ))}
        </div>
      </div>
    </section>
  );
}


function TimelineLine({
  config,
  data,
  line,
  range,
  height,
  currentMarker,
  pastDay,
  selectedSlot,
  mobileActive,
  showNowLabel,
  onChooseSlot,
}) {
  const lineClosures = data.closures.filter(
    (closure) => closure.work_line_id === null || closure.work_line_id === line.id,
  );

  return (
    <div
      className={`agenda-timeline__line${mobileActive ? " is-mobile-active" : ""}`}
      style={{ height }}
    >
      {lineClosures.map((closure) => (
        <div
          key={`closure-${closure.id}`}
          className="agenda-timeline__closure"
          style={timelinePosition(
            closure.start_time,
            closure.end_time,
            range,
            ROW_HEIGHT,
          )}
        >
          <strong>{closure.type_label}</strong>
          {closure.reason ? <span>{closure.reason}</span> : null}
        </div>
      ))}

      {line.available_slots.map((slot) => {
        const isSelected = Boolean(selectedSlot
          && selectedSlot.work_line_id === slot.work_line_id
          && selectedSlot.starts_at === slot.starts_at);
        return (
          <button
            key={`slot-${slot.starts_at}`}
            type="button"
            className={`agenda-timeline__slot${isSelected ? " is-selected" : ""}`}
            style={markerPosition(slot.starts_at, range, ROW_HEIGHT)}
            aria-label={`Elegir ${formatClock(slot.starts_at)} en ${line.name}`}
            aria-pressed={isSelected}
            title={`${formatClock(slot.starts_at)} · ${SLOT_REASON_COPY[slot.reason] || "Hueco válido"}`}
            onClick={() => onChooseSlot(slot)}
          >
            <span>{formatClock(slot.starts_at)}</span>
          </button>
        );
      })}

      {line.appointments.map((appointment) => {
        const isPast = pastDay || Boolean(
          currentMarker && appointment.ends_at <= currentMarker,
        );
        const inProgress = Boolean(
          currentMarker
          && appointment.starts_at < currentMarker
          && appointment.ends_at > currentMarker,
        );
        return (
          <a
            key={appointment.id}
            className={`agenda-appointment is-${appointment.status}${isPast ? " is-past" : ""}${inProgress ? " is-current" : ""}`}
            style={timelinePosition(
              appointment.starts_at,
              appointment.ends_at,
              range,
              ROW_HEIGHT,
            )}
            href={appointmentDetailUrl(config.appointmentUrlTemplate, appointment.id)}
            aria-label={`${appointment.client.name}, ${formatClock(appointment.starts_at)}, ${appointment.status_label}`}
          >
            <time>{formatClock(appointment.starts_at)}–{formatClock(appointment.ends_at)}</time>
            <strong>{appointment.client.name}</strong>
            <span>{appointment.service_summary || "Cita"}</span>
            <small>{appointment.status_label}</small>
          </a>
        );
      })}

      {currentMarker && !pastDay ? (
        <div
          className="agenda-timeline__now"
          style={markerPosition(currentMarker, range, ROW_HEIGHT)}
          aria-label={`Corte temporal a las ${formatClock(currentMarker)}`}
        >
          {showNowLabel ? <span>Ahora</span> : null}
        </div>
      ) : null}
    </div>
  );
}


function ResourceError({ message, onRetry, compact = false }) {
  return (
    <div className={`agenda-resource-error${compact ? " is-compact" : ""}`} role="alert">
      <strong>No hemos podido actualizar la agenda.</strong>
      <p>{message}</p>
      <button type="button" onClick={onRetry}>Intentar de nuevo</button>
    </div>
  );
}


function AgendaSkeleton() {
  return (
    <div className="agenda-skeleton" role="status" aria-live="polite">
      <span />
      <span />
      <span />
      <p>Preparando la jornada…</p>
    </div>
  );
}
