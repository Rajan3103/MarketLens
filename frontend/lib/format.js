// Numbers the Indian way: Rs 1,23,456.50, 12.4 %, 1.2 Cr. Empty values show as "–".

const EMPTY = "–";

// True for null, undefined, "" and NaN.
export function isEmpty(value) {
  return value === null || value === undefined || value === "" || (typeof value === "number" && Number.isNaN(value));
}

// 123456.5 -> "1,23,456.50" (Indian grouping).
export function formatNumber(value, decimals = 2) {
  if (isEmpty(value)) return EMPTY;
  return Number(value).toLocaleString("en-IN", { minimumFractionDigits: decimals, maximumFractionDigits: decimals });
}

// 123456.5 -> "Rs 1,23,456.50"; negative -> "-Rs 1,234.00".
export function formatRs(value, decimals = 2) {
  if (isEmpty(value)) return EMPTY;
  const n = Number(value);
  return `${n < 0 ? "-" : ""}Rs ${formatNumber(Math.abs(n), decimals)}`;
}

// 12.345 -> "12.35 %" (the value is already in percent); signed adds "+" for gains.
export function formatPct(value, decimals = 2, signed = false) {
  if (isEmpty(value)) return EMPTY;
  const n = Number(value);
  return `${signed && n > 0 ? "+" : ""}${formatNumber(n, decimals)} %`;
}

// A big whole number (volume): 4,23,18,217.
export function formatInt(value) {
  return formatNumber(value, 0);
}

// "2026-11-03" -> "03-Nov-2026" (dates stay as text from the API, no time-zone surprises).
export function formatDate(value) {
  if (isEmpty(value)) return EMPTY;
  const [y, m, d] = String(value).slice(0, 10).split("-");
  const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  return m ? `${d}-${months[Number(m) - 1]}-${y}` : String(value);
}

// Format a cell by its kind: "rs", "pct", "pct_signed", "int", "number", "date" or "text".
export function formatValue(value, kind = "text", decimals) {
  switch (kind) {
    case "rs": return formatRs(value, decimals ?? 2);
    case "pct": return formatPct(value, decimals ?? 2);
    case "pct_signed": return formatPct(value, decimals ?? 2, true);
    case "int": return formatInt(value);
    case "number": return formatNumber(value, decimals ?? 2);
    case "date": return formatDate(value);
    default: return isEmpty(value) ? EMPTY : String(value);
  }
}
