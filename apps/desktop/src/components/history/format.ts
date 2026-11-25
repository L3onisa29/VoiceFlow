// Italian display formats shared by the history list, recents and detail.
export const numberFormat = new Intl.NumberFormat("it-IT", {
  maximumFractionDigits: 0,
});
const dateTime = new Intl.DateTimeFormat("it-IT", {
  dateStyle: "medium",
  timeStyle: "short",
});
const time = new Intl.DateTimeFormat("it-IT", { timeStyle: "short" });
const day = new Intl.DateTimeFormat("it-IT", {
  weekday: "long",
  day: "numeric",
  month: "long",
});
const dayWithYear = new Intl.DateTimeFormat("it-IT", {
  weekday: "long",
  day: "numeric",
  month: "long",
  year: "numeric",
});

export const formatDateTime = (timestamp: number) => dateTime.format(timestamp);
export const formatTime = (timestamp: number) => time.format(timestamp);
export const isoDate = (timestamp: number) => new Date(timestamp).toISOString();
export const formatWords = (count: number) =>
  count === 1 ? "1 parola" : `${numberFormat.format(count)} parole`;

const startOfDay = (timestamp: number) => {
  const date = new Date(timestamp);
  date.setHours(0, 0, 0, 0);
  return date.getTime();
};

/** "Oggi", "Ieri" or the full local date, capitalised for a heading. */
export function dayLabel(timestamp: number, now = Date.now()) {
  const today = startOfDay(now);
  const target = startOfDay(timestamp);
  if (target === today) return "Oggi";
  // Compare calendar days, not 24 h spans (DST-safe).
  const yesterday = new Date(today);
  yesterday.setDate(yesterday.getDate() - 1);
  if (target === yesterday.getTime()) return "Ieri";
  const sameYear =
    new Date(target).getFullYear() === new Date(today).getFullYear();
  const label = (sameYear ? day : dayWithYear).format(timestamp);
  return label.charAt(0).toUpperCase() + label.slice(1);
}

/** Groups entries already sorted newest first by their local calendar day. */
export function groupByDay<T extends { createdAt: number }>(entries: T[]) {
  const groups: { key: number; label: string; entries: T[] }[] = [];
  const now = Date.now();
  for (const entry of entries) {
    const key = startOfDay(entry.createdAt);
    const last = groups[groups.length - 1];
    if (last?.key === key) last.entries.push(entry);
    else
      groups.push({
        key,
        label: dayLabel(entry.createdAt, now),
        entries: [entry],
      });
  }
  return groups;
}
