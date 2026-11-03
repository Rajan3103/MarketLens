// One number on a card: a label, the value, and a change in green (up) or red (down).
import { formatPct, formatRs, isEmpty } from "@/lib/format";

export default function MetricCard({ label, value, change, changeKind = "pct", help }) {
  const n = isEmpty(change) ? null : Number(change);
  const color = n === null ? "" : n > 0 ? "text-green-600 dark:text-green-400" : n < 0 ? "text-red-600 dark:text-red-400"
    : "text-slate-500";
  const changeText = n === null ? null : changeKind === "rs" ? `${n > 0 ? "+" : ""}${formatRs(n)}` : formatPct(n, 2, true);
  return (
    <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800" title={help}>
      <p className="text-xs font-medium uppercase tracking-wide text-slate-500">{label}</p>
      <p className="mt-1 text-xl font-bold">{isEmpty(value) ? "–" : value}</p>
      {changeText && (
        <p className={`text-sm font-medium ${color}`}>{n > 0 ? "▲" : n < 0 ? "▼" : ""} {changeText}</p>
      )}
    </div>
  );
}
