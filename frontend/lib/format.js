// Numbers, dates and times the Indian way: Rs 1,23,45,678.50, +12.40 %, 16-Nov-2026, 09:15 IST.
// Empty values (null, undefined, "", NaN) show as "–". Tests: npm test   (from the frontend folder)

const EMPTY = "–";
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const IST = "Asia/Kolkata";

// True for null, undefined, "" and NaN.
export function isEmpty(value) {
  return value === null || value === undefined || value === "" || (typeof value === "number" && Number.isNaN(value));
}

// 12345678.5 -> "1,23,45,678.50" (Indian grouping: lakhs and crores).
export function formatNumber(value, decimals = 2) {
  if (isEmpty(value)) return EMPTY;
  return Number(value).toLocaleString("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

// 12345678.5 -> "Rs 1,23,45,678.50"; negative -> "-Rs 1,234.00"; signed adds "+" for gains ("+Rs 500.00").
export function formatRs(value, decimals = 2, signed = false) {
  if (isEmpty(value)) return EMPTY;
  const n = Number(value);
  const sign = n < 0 ? "-" : signed && n > 0 ? "+" : "";
  return `${sign}Rs ${formatNumber(Math.abs(n), decimals)}`;
}

// 12.345 -> "12.35 %" (the value is already in percent); signed adds "+" for gains ("+12.35 %").
export function formatPct(value, decimals = 2, signed = false) {
  if (isEmpty(value)) return EMPTY;
  const n = Number(value);
  return `${signed && n > 0 ? "+" : ""}${formatNumber(n, decimals)} %`;
}

// A big whole number (volume): 4,23,18,217.
export function formatInt(value) {
  return formatNumber(value, 0);
}

// The parts of a moment in India time: { year, month (1-12), day, hour, minute }.
function istParts(date) {
  const parts = new Intl.DateTimeFormat("en-GB", {
    timeZone: IST, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(date);
  const get = (type) => Number(parts.find((p) => p.type === type).value);
  return { year: get("year"), month: get("month"), day: get("day"), hour: get("hour"), minute: get("minute") };
}

// A moment as a Date: ISO text with a time ("2026-11-16T09:15:00+05:30"), a Date, or milliseconds since 1970.
// Returns null for empty or unreadable values.
function toMoment(value) {
  if (isEmpty(value)) return null;
  const date = value instanceof Date ? value : new Date(value);
  return Number.isNaN(date.getTime()) ? null : date;
}

// "2026-11-16" -> "16-Nov-2026". A plain date stays as it is (no time-zone surprises); a moment with a time
// ("2026-11-15T20:00:00Z") is shown as its date in India (16-Nov-2026).
export function formatDate(value) {
  if (isEmpty(value)) return EMPTY;
  const text = value instanceof Date ? "" : String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(text.slice(0, 10)) && !text.includes("T")) {
    const [y, m, d] = text.slice(0, 10).split("-");
    return `${d}-${MONTHS[Number(m) - 1]}-${y}`;
  }
  const date = toMoment(value);
  if (!date) return String(value);
  const p = istParts(date);
  return `${String(p.day).padStart(2, "0")}-${MONTHS[p.month - 1]}-${p.year}`;
}

// A moment as India time: "09:15 IST" (24-hour clock).
export function formatTimeIST(value) {
  const date = toMoment(value);
  if (!date) return EMPTY;
  const p = istParts(date);
  return `${String(p.hour).padStart(2, "0")}:${String(p.minute).padStart(2, "0")} IST`;
}

// How long ago: "just now", "5 min ago", "3 h ago", "2 days ago" (or "in 5 min" for the future);
// more than a week away shows the date. `now` is for tests.
export function formatRelative(value, now = Date.now()) {
  const date = toMoment(value);
  if (!date) return EMPTY;
  const seconds = Math.round((Number(now) - date.getTime()) / 1000);
  const away = Math.abs(seconds);
  const say = (amount, unit) => (seconds >= 0 ? `${amount} ${unit} ago` : `in ${amount} ${unit}`);
  if (away < 45) return "just now";
  if (away < 3600) return say(Math.max(1, Math.round(away / 60)), "min");
  if (away < 86400) return say(Math.round(away / 3600), "h");
  if (away < 7 * 86400) {
    const days = Math.round(away / 86400);
    return say(days, days === 1 ? "day" : "days");
  }
  return formatDate(date);
}

// The text colour for a profit or loss: text-profit above 0, text-loss below 0, text-muted-foreground otherwise.
export function pnlClass(value) {
  if (isEmpty(value)) return "text-muted-foreground";
  const n = Number(value);
  if (n > 0) return "text-profit";
  if (n < 0) return "text-loss";
  return "text-muted-foreground";
}

// Format a cell by its kind: "rs", "rs_signed", "pct", "pct_signed", "int", "number", "date", "time",
// "relative" or "text".
export function formatValue(value, kind = "text", decimals) {
  switch (kind) {
    case "rs": return formatRs(value, decimals ?? 2);
    case "rs_signed": return formatRs(value, decimals ?? 2, true);
    case "pct": return formatPct(value, decimals ?? 2);
    case "pct_signed": return formatPct(value, decimals ?? 2, true);
    case "int": return formatInt(value);
    case "number": return formatNumber(value, decimals ?? 2);
    case "date": return formatDate(value);
    case "time": return formatTimeIST(value);
    case "relative": return formatRelative(value);
    default: return isEmpty(value) ? EMPTY : String(value);
  }
}
