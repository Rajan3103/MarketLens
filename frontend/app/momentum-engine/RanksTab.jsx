"use client";
// Current ranks: every stock of the chosen list ranked at the last stored close with the form's ranking
// settings. Historical model output for learning, not advice. Data: GET /api/momentum/ranks.
import DataTable from "@/components/DataTable";
import ErrorBox from "@/components/ErrorBox";
import Loading from "@/components/Loading";
import MetricCard from "@/components/MetricCard";
import { apiGet } from "@/lib/api";
import { formatDate, formatNumber } from "@/lib/format";
import usePolling from "@/lib/usePolling";

const COLUMNS = [
  { key: "rank", label: "Rank", kind: "int" },
  { key: "symbol", label: "Stock", kind: "text",
    render: (r) => (r.in_top_n ? <strong className="text-blue-700 dark:text-blue-300">{r.symbol}</strong> : r.symbol) },
  { key: "name", label: "Name", kind: "text" },
  { key: "sector", label: "Sector", kind: "text" },
  { key: "score_pct", label: "Momentum score", kind: "pct_signed", colorSign: true },
  { key: "passed", label: "Filters", kind: "text", render: (r) => (r.passed ? "✓ passed" : "✗") },
  { key: "reason", label: "Why not", kind: "text" },
];

export default function RanksTab({ settings }) {
  const query = `momentum/ranks?${new URLSearchParams({ settings: JSON.stringify(settings) })}`;
  const { data, error, loading, refresh } = usePolling(() => apiGet(query), 0, { key: query });

  return (
    <div className="space-y-4">
      <div role="note" className="rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900
        dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
        <strong>Historical model output, not advice.</strong> These ranks are worked out from past prices with the
        settings on the Backtest tab. They are not a recommendation to buy or sell anything.
      </div>
      {error && !data && <ErrorBox error={error} onRetry={refresh} />}
      {!data && !error && <Loading text="Ranking the stocks…" />}
      {data && (
        <>
          {loading && <p className="text-xs text-slate-500">Updating…</p>}
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            <MetricCard label="Ranked at the close of" value={formatDate(data.as_of)} />
            <MetricCard label="Passed the filters" value={`${data.rows.filter((r) => r.passed).length} of ${data.rows.length}`} />
            <MetricCard label="Top N shown in bold" value={data.top_n} />
            <MetricCard label="Nifty 50 filter" value={!data.index_filter ? "off"
              : data.market_ok ? "✓ above its average" : `✗ below: model holds ${data.hold === "CASH" ? "cash" : data.hold}`}
              help={data.index_filter ? `Nifty 50 ${formatNumber(data.index_close)} vs average ${formatNumber(data.index_average)}` : undefined} />
          </div>
          <DataTable columns={COLUMNS} rows={data.rows} pageSize={25} rowKey={(r) => r.symbol} />
          <p className="text-xs text-slate-500">{data.note}</p>
        </>
      )}
    </div>
  );
}
