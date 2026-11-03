// One number on a card: a label, the value, and a change in green (up) or red (down).
//   changeText: your own words for the change (e.g. "+120.50 (+0.48 %)"); its colour still comes from `change`.
//   invert:     a rise is bad news (India VIX), so show it in red.
import { formatPct, formatRs, isEmpty } from "@/lib/format";

export default function MetricCard({ label, value, change, changeKind = "pct", changeText, invert = false, help }) {
  const n = isEmpty(change) ? null : Number(change);
  const good = n === null ? null : invert ? n < 0 : n > 0;
  const bad = n === null ? null : invert ? n > 0 : n < 0;
  const color = good ? "text-green-600 dark:text-green-400" : bad ? "text-red-600 dark:text-red-400" : "text-slate-500";
  const text = n === null ? null
    : changeText ?? (changeKind === "rs" ? `${n > 0 ? "+" : ""}${formatRs(n)}` : formatPct(n, 2, true));
  return (
    <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800" title={help}>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-bold">{isEmpty(value) ? "–" : value}</p>
      {text && (
        <p className={`text-sm font-medium ${color}`}>{n > 0 ? "▲" : n < 0 ? "▼" : ""} {text}</p>
      )}
    </div>
  );
}
