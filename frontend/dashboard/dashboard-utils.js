export function normalizeText(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLocaleLowerCase("es");
}

export function businessMatchesFilter(business, filter) {
  if (filter === "all") return true;
  if (filter === "pending_closure") return business.counts.pending_closure > 0;
  return business.health.code === filter;
}

export function filterBusinesses(businesses, filter, query) {
  const normalizedQuery = normalizeText(query).trim();
  return businesses.filter((business) => {
    const matchesQuery = !normalizedQuery
      || normalizeText(`${business.name} ${business.city}`).includes(normalizedQuery);
    return matchesQuery && businessMatchesFilter(business, filter);
  });
}

export function sortBusinesses(businesses) {
  const priority = {
    setup_pending: 0,
    operational: 2,
    inactive: 3,
  };
  return [...businesses].sort((left, right) => {
    const leftPriority = left.counts.pending_closure > 0
      ? 1
      : (priority[left.health.code] ?? 4);
    const rightPriority = right.counts.pending_closure > 0
      ? 1
      : (priority[right.health.code] ?? 4);
    return leftPriority - rightPriority || left.name.localeCompare(right.name, "es");
  });
}

export function barPercent(value, maximum) {
  if (!value || !maximum) return 0;
  return Math.max(8, Math.round((value / maximum) * 100));
}

export function formatDateTime(value) {
  if (!value) return "Sin movimientos registrados";
  return new Intl.DateTimeFormat("es-ES", {
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}

export function formatShortDate(value) {
  return new Intl.DateTimeFormat("es-ES", {
    day: "numeric",
    month: "short",
  }).format(new Date(`${value}T12:00:00`));
}

export function pluralize(value, singular, plural) {
  return `${value} ${value === 1 ? singular : plural}`;
}
