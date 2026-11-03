"use client";
// Top of the page: market open / closed, the 5 index cards, Nifty 50 top gainers and losers, and breadth.
// Data: GET /api/market/snapshot. Fetching it takes about 8 seconds, so the API does it as a background job:
// the page shows the last snapshot at once and a progress bar while the new one is fetched.
// Refreshes every 60 seconds in market hours (like st.fragment(run_every=60) on the Streamlit page).
import { useCallback, useState } from "react";

import DataTable from "@/components/DataTable";
import ErrorBox from "@/components/ErrorBox";
import Loading from "@/components/Loading";
import MetricCard from "@/components/MetricCard";
import PlotlyChart from "@/components/PlotlyChart";
import ProgressBar from "@/components/ProgressBar";
import { apiGet } from "@/lib/api";
import { formatNumber, formatPct, isEmpty } from "@/lib/format";
import usePolling from "@/lib/usePolling";

const MOVER_COLUMNS = [
  { key: "symbol", label: "Stock", kind: "text", render: (r) => <span title={r.name}>{r.symbol}</span> },
  { key: "last", label: "Price", kind: "number" },
  { key: "change", label: "Change", kind: "number", colorSign: true,
    render: (r) => `${r.change > 0 ? "+" : ""}${formatNumber(r.change)}` },
  { key: "pct_change", label: "% Change", kind: "pct_signed", colorSign: true },
];

// One index card: price, then the change in points and % (VIX rising shows red).
function IndexCard({ card }) {
  if (isEmpty(card.last) || isEmpty(card.prev_close)) return <MetricCard label={card.name} value="no data" />;
  const sign = card.change > 0 ? "+" : "";
  return (
    <MetricCard label={card.name} value={formatNumber(card.last)} change={card.change} invert={card.inverse}
      changeText={`${sign}${formatNumber(card.change)} (${formatPct(card.pct_change, 2, true)})`} />
  );
}

// Green banner when the market is open, grey when it is closed.
function StatusBanner({ open, status }) {
  return (
    <div className={`rounded-lg border px-4 py-2 text-sm ${open
      ? "border-green-300 bg-green-50 text-green-800 dark:border-green-800 dark:bg-green-950 dark:text-green-200"
      : "border-slate-300 bg-slate-50 text-slate-700 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-300"}`}>
      {open ? "🟢" : "⚪"} {status}
    </div>
  );
}

export default function LiveSection({ marketOpen }) {
  const { data, error, refresh } = usePolling(() => apiGet("market/snapshot"), 60, { enabled: marketOpen });
  const snap = data?.snapshot;
  const [failedJob, setFailedJob] = useState(null);

  // When the job has finished, ask again: the API now has the new snapshot. A failed job waits for "Try again".
  const jobDone = useCallback((job) => {
    if (job.status === "done") refresh();
    else setFailedJob(job.job_id);
  }, [refresh]);

  if (error && !data) return <ErrorBox error={error} onRetry={refresh} />;
  if (!data) return <Loading text="Fetching prices…" />;
  const b = snap?.breadth;

  return (
    <section className="space-y-4">
      <StatusBanner open={data.open} status={data.status} />
      {data.job_id && (
        <div className="space-y-2">
          <ProgressBar jobId={data.job_id} onDone={jobDone} />
          {failedJob === data.job_id && (
            <button onClick={refresh} className="text-sm text-blue-600 hover:underline dark:text-blue-400">
              Try again
            </button>
          )}
        </div>
      )}
      {!snap && !data.job_id && <ErrorBox message="No snapshot yet." onRetry={refresh} />}

      {snap && (
        <>
          <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-5">
            {snap.indices.map((card) => <IndexCard key={card.symbol} card={card} />)}
          </div>
          <p className="text-xs text-slate-500">
            Source: {snap.source} · fetched {snap.as_of_text}.{snap.key_note ? ` ${snap.key_note}` : ""}
          </p>

          <div className="grid gap-4 md:grid-cols-2">
            <div>
              <h2 className="mb-2 font-semibold">🟢 Top 5 gainers (Nifty 50)</h2>
              <DataTable columns={MOVER_COLUMNS} rows={snap.gainers} pageSize={5} rowKey={(r) => r.symbol}
                emptyText="No Nifty 50 stock is up." />
            </div>
            <div>
              <h2 className="mb-2 font-semibold">🔴 Top 5 losers (Nifty 50)</h2>
              <DataTable columns={MOVER_COLUMNS} rows={snap.losers} pageSize={5} rowKey={(r) => r.symbol}
                emptyText="No Nifty 50 stock is down." />
            </div>
          </div>

          <div className="space-y-3">
            <h2 className="font-semibold">Market breadth (Nifty 50, {snap.stock_count} stocks)</h2>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <MetricCard label="Advances" value={b.advances} />
              <MetricCard label="Declines" value={b.declines} />
              <MetricCard label="Unchanged" value={b.unchanged} />
              <MetricCard label="Advance / decline ratio" value={b.ad_ratio ?? "all up"} />
            </div>
            <PlotlyChart figure={snap.breadth_chart} height={120} />
          </div>
        </>
      )}
    </section>
  );
}
