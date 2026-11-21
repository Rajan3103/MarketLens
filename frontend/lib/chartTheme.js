"use client";
// The theme's colours and font for charts (lightweight-charts and Plotly), read from the CSS variables in
// app/globals.css, so charts follow the light / dark switch. Browser only: call it inside an effect or after
// the theme changed (next-themes' resolvedTheme), never on the server.
//
//   const t = readChartTheme();   t.profit, t.loss, t.series[0], t.font ...

// Fallback colours (the dark theme) when the page has no styles yet.
const FALLBACK = {
  background: "#0b0f17", foreground: "#e6e9ef", card: "#121826", muted: "#1c2333", mutedForeground: "#9ba5b7",
  border: "#263042", input: "#2f3a4f", primary: "#60a5fa", popover: "#121826", profit: "#34d399", loss: "#f87171",
  warning: "#fbbf24", series: ["#3b82f6", "#f97316", "#14b8a6", "#a78bfa", "#ec4899"],
  font: "ui-sans-serif, system-ui, sans-serif", mono: "ui-monospace, monospace",
};

// Every colour the charts use, from the current theme.
export function readChartTheme() {
  if (typeof window === "undefined") return FALLBACK;
  const css = getComputedStyle(document.documentElement);
  const get = (name, fallback) => css.getPropertyValue(`--${name}`).trim() || fallback;
  return {
    background: get("background", FALLBACK.background),
    foreground: get("foreground", FALLBACK.foreground),
    card: get("card", FALLBACK.card),
    muted: get("muted", FALLBACK.muted),
    mutedForeground: get("muted-foreground", FALLBACK.mutedForeground),
    border: get("border", FALLBACK.border),
    input: get("input", FALLBACK.input),
    primary: get("primary", FALLBACK.primary),
    popover: get("popover", FALLBACK.popover),
    profit: get("profit", FALLBACK.profit),
    loss: get("loss", FALLBACK.loss),
    warning: get("warning", FALLBACK.warning),
    series: [1, 2, 3, 4, 5].map((i) => get(`chart-${i}`, FALLBACK.series[i - 1])),
    font: getComputedStyle(document.body).fontFamily || FALLBACK.font,
    mono: css.getPropertyValue("--font-mono").trim() || FALLBACK.mono,
  };
}

// "#34d399" + 0.4 -> "rgba(52, 211, 153, 0.4)" (for see-through volume bars and area fills).
export function withAlpha(hex, alpha) {
  const h = String(hex).replace("#", "");
  if (h.length !== 6) return hex;
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16));
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

const IST_SECONDS = 5.5 * 3600;

// A time from the API as lightweight-charts wants it: "2026-11-16" stays a business day; a moment
// ("2026-11-16T09:15:00+05:30", seconds or milliseconds) becomes seconds shifted to India time, because the
// chart always draws its axis in UTC.
export function toChartTime(value) {
  if (typeof value === "number") return Math.floor(value > 1e12 ? value / 1000 : value) + IST_SECONDS;
  const text = String(value);
  if (/^\d{4}-\d{2}-\d{2}$/.test(text)) return text;
  return Math.floor(Date.parse(text) / 1000) + IST_SECONDS;
}

// A chart time back to a Date whose UTC fields are the India date and time (for labels).
export function chartTimeToDate(time) {
  if (typeof time === "string") return new Date(`${time}T00:00:00Z`);
  if (typeof time === "object" && time) return new Date(Date.UTC(time.year, time.month - 1, time.day));
  return new Date(time * 1000);
}
