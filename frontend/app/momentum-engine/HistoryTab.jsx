"use client";
// Rebalance history: what a saved run bought and sold on each rebalance day.
// Data: GET /api/momentum/runs (the list) and GET /api/momentum/runs/{run_id}/rebalances.
import { useState } from "react";

import DataTable from "@/components/DataTable";
import ErrorBox from "@/components/ErrorBox";
import FormField, { INPUT_CLASS } from "@/components/FormField";
import Loading from "@/components/Loading";
import { apiGet } from "@/lib/api";
import { formatDate, formatInt, formatPct, formatRs } from "@/lib/format";
import usePolling from "@/lib/usePolling";

// One line of chips: "TCS 12 @ Rs 3,512.00".
function Moves({ moves, tone }) {
  if (!moves.length) return <span className="text-slate-400">–</span>;
  const color = tone === "buy"
    ? "border-green-300 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-950 dark:text-green-300"
    : "border-red-300 bg-red-50 text-red-800 dark:border-red-800 dark:bg-red-950 dark:text-red-300";
  return (
    <div className="flex flex-wrap gap-1">
      {moves.map((m) => (
        <span key={m.symbol} className={`rounded border px-1.5 py-0.5 text-xs ${color}`}
          title={`${formatInt(m.qty)} shares${m.price ? ` at ${formatRs(m.price)}` : ""}`}>
          {m.symbol} {formatInt(m.qty)}{m.price ? ` @ ${formatRs(m.price)}` : ""}
        </span>
      ))}
    </div>
  );
}

const COLUMNS = [
  { key: "date", label: "Date", kind: "date" },
  { key: "buys", label: "Bought", kind: "text", render: (r) => <Moves moves={r.buys} tone="buy" /> },
  { key: "sells", label: "Sold", kind: "text", render: (r) => <Moves moves={r.sells} tone="sell" /> },
  { key: "holdings", label: "Holdings", kind: "int" },
  { key: "value", label: "Value held", kind: "rs", decimals: 0 },
];

// The rebalances of one run.
function RunHistory({ runId }) {
  const { data, error, refresh } = usePolling(() => apiGet(`momentum/runs/${runId}/rebalances`), 0, { key: String(runId) });
  if (error && !data) return <ErrorBox error={error} onRetry={refresh} />;
  if (!data || data.run_id !== runId) return <Loading text="Reading the rebalances…" />;
  const rows = [...data.rebalances].reverse();
  return (
    <div className="space-y-2">
      <p className="text-sm text-slate-500">
        {rows.length} rebalances, newest first. Each trade is at the close of the day after the signal.
      </p>
      <DataTable columns={COLUMNS} rows={rows} pageSize={15} rowKey={(r) => r.date}
        emptyText="This run has no saved holdings." />
    </div>
  );
}

export default function HistoryTab({ runId: latestRunId }) {
  const [picked, setPicked] = useState(null);
  const { data, error, refresh } = usePolling(() => apiGet("momentum/runs"), 0, { key: String(latestRunId) });
  const runs = data?.runs || [];
  const runId = picked ?? latestRunId ?? runs[0]?.run_id ?? null;

  return (
    <div className="space-y-4">
      {error && !data && <ErrorBox error={error} onRetry={refresh} />}
      {data && !runs.length && (
        <p className="text-sm text-slate-500">No saved momentum runs yet. Run a backtest on the Backtest tab first.</p>
      )}
      {runs.length > 0 && (
        <FormField label="Run" htmlFor="me-run">
          <select id="me-run" value={runId ?? ""} onChange={(e) => setPicked(Number(e.target.value))} className={`${INPUT_CLASS} max-w-full`}>
            {runs.map((r) => (
              <option key={r.run_id} value={r.run_id}>
                Run {r.run_id}: {r.name || "momentum"} · {formatDate(r.start)} to {formatDate(r.end)} · CAGR {formatPct(r.cagr_pct)}
              </option>
            ))}
          </select>
        </FormField>
      )}
      {runId && <RunHistory key={runId} runId={runId} />}
    </div>
  );
}
