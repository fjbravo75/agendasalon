const MINUTES_PER_DAY = 24 * 60;


export function dateParts(dateValue) {
  const [year, month, day] = dateValue.split("-").map(Number);
  return { year, month, day };
}


export function monthFromDate(dateValue) {
  const { year, month } = dateParts(dateValue);
  return { year, month };
}


export function shiftMonth({ year, month }, amount) {
  const shifted = new Date(year, month - 1 + amount, 1, 12);
  return {
    year: shifted.getFullYear(),
    month: shifted.getMonth() + 1,
  };
}


export function sameMonth(left, right) {
  return left.year === right.year && left.month === right.month;
}


export function buildMonthCells(days) {
  if (!days.length) {
    return [];
  }
  const { year, month } = dateParts(days[0].date);
  const nativeWeekday = new Date(year, month - 1, 1, 12).getDay();
  const mondayOffset = (nativeWeekday + 6) % 7;
  return [
    ...Array.from({ length: mondayOffset }, (_, index) => ({
      key: `empty-${index}`,
      empty: true,
    })),
    ...days.map((day) => ({ ...day, key: day.date, empty: false })),
  ];
}


export function clockMinutes(value) {
  if (!value) {
    return null;
  }
  const match = value.match(/T(\d{2}):(\d{2})/) || value.match(/^(\d{2}):(\d{2})/);
  if (!match) {
    return null;
  }
  return Number(match[1]) * 60 + Number(match[2]);
}


export function formatClock(value) {
  if (!value) {
    return "";
  }
  const match = value.match(/T(\d{2}:\d{2})/) || value.match(/^(\d{2}:\d{2})/);
  return match ? match[1] : "";
}


export function formatDate(dateValue, options) {
  const { year, month, day } = dateParts(dateValue);
  return new Intl.DateTimeFormat("es-ES", options).format(
    new Date(year, month - 1, day, 12),
  );
}


export function capitalizeFirst(value) {
  return value ? `${value.charAt(0).toUpperCase()}${value.slice(1)}` : value;
}


export function getTimelineRange(dayData) {
  const starts = [];
  const ends = [];

  dayData?.work_lines?.forEach((line) => {
    line.appointments.forEach((appointment) => {
      starts.push(clockMinutes(appointment.starts_at));
      ends.push(clockMinutes(appointment.ends_at));
    });
    line.available_slots.forEach((slot) => {
      starts.push(clockMinutes(slot.starts_at));
      ends.push(clockMinutes(slot.ends_at));
    });
  });
  dayData?.closures?.forEach((closure) => {
    starts.push(clockMinutes(closure.start_time));
    ends.push(clockMinutes(closure.end_time));
  });

  const cleanStarts = starts.filter(Number.isFinite);
  const cleanEnds = ends.filter(Number.isFinite);
  const earliest = cleanStarts.length ? Math.min(...cleanStarts) : 9 * 60;
  const latest = cleanEnds.length ? Math.max(...cleanEnds) : 20 * 60;
  const start = Math.max(0, Math.min(8 * 60, Math.floor(earliest / 60) * 60));
  const end = Math.min(
    MINUTES_PER_DAY,
    Math.max(20 * 60, Math.ceil(latest / 60) * 60),
  );
  return { start, end };
}


export function timelinePosition(startValue, endValue, range, rowHeight = 24) {
  const startMinutes = Math.max(clockMinutes(startValue) ?? range.start, range.start);
  const endMinutes = Math.min(clockMinutes(endValue) ?? range.end, range.end);
  const top = ((startMinutes - range.start) / 15) * rowHeight;
  const height = Math.max(((endMinutes - startMinutes) / 15) * rowHeight, rowHeight);
  return { top: `${top}px`, height: `${height}px` };
}


export function markerPosition(startValue, range, rowHeight = 24) {
  const startMinutes = clockMinutes(startValue) ?? range.start;
  return { top: `${((startMinutes - range.start) / 15) * rowHeight + 2}px` };
}


export function timelineHeight(range, rowHeight = 24) {
  return ((range.end - range.start) / 15) * rowHeight;
}


export function hourLabels(range) {
  const labels = [];
  for (let minutes = range.start; minutes <= range.end; minutes += 60) {
    labels.push({
      minutes,
      label: `${String(Math.floor(minutes / 60)).padStart(2, "0")}:00`,
    });
  }
  return labels;
}


export function buildAppointmentAssistantUrl(baseUrl, slot, fallbackDate) {
  const params = new URLSearchParams({
    prefill_from_agenda: "1",
    target_date: slot?.starts_at?.slice(0, 10) || fallbackDate,
  });
  if (slot) {
    params.set("selected_work_line_id", String(slot.work_line_id));
    params.set("selected_starts_at", slot.starts_at);
  }
  return `${baseUrl}?${params.toString()}`;
}


export function appointmentDetailUrl(template, appointmentId) {
  return template.replace("__appointment_id__", String(appointmentId));
}
