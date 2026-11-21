"use client";
// The momentum engine's own result tabs, added after the shared ones in <BacktestResults>: strategy vs Nifty 50
// (costs, tax, the comparison table), holdings at the end, every rebalance, and tax by financial year.
//
//   <BacktestResults result={result} extraTabs={portfolioTabs(result)} />
import { Briefcase, Repeat, Scale, Receipt } from "lucide-react";

import DataTable from "@/components/DataTable";
import MetricCard from "@/components/MetricCard";
import MetricGrid from "@/components/MetricGrid";
import { formatInt, formatNumber, formatRs, isEmpty, pnlClass } from "@/lib/format";

const HOLDING_COLUMNS = [
  { key: "symbol", label: "Holding", kind: "text" },
  { key: "qty", label: "Shares", kind: "int" },
  { key: "avg_price", label: "Avg buy price", kind: "rs" },
  { key: "price", label: "Last price", kind: "rs" },
  { key: "value", label: "Value", kind: "rs", decimals: 0 },
  { key: "weight_pct", label: "Weight", kind: "pct" },
  { key: "rank", label: "Rank", kind: "int" },
];
const REBALANCE_COLUMNS = [
  { key: "signal_day", label: "Signal (close)", kind: "date" },
  { key: "trade_day", label: "Traded (next close)", kind: "date" },
  { key: "sold", label: "Sold", kind: "text" },
  { key: "bought", label: "Bought", kind: "text" },
  { key: "stocks", label: "Stocks held", kind: "int" },
  { key: "market_ok", label: "Nifty filter", kind: "text", render: (r) => (r.market_ok ? "✓ invested" : "✗ safe asset") },
  { key: "tax_paid", label: "Tax paid", kind: "rs", decimals: 0 },
  { key: "value_after", label: "Value after", kind: "rs", decimals: 0 },
];

// Columns of a table whose first column is text and the rest are numbers (comparison, tax).
function columnsOf(rows, first) {
  if (!rows?.length) return [];
  return Object.keys(rows[0]).map((key) => (key === first ? { key, label: key, kind: "text" }
    : { key, label: key, kind: "number" }));
}

// Strategy vs Nifty 50: the cost and tax tiles, then the side-by-side table.
function VsNifty({ result }) {
  const s = result.summary || {};
  const beat = isEmpty(s.benchmark_return_pct) ? null : s.total_return_pct - s.benchmark_return_pct;
  return (
    <div className="space-y-4">
      <MetricGrid columns={4}>
        <MetricCard label="Final value" value={formatRs(s.final_value, 0)} change={s.total_return_pct} />
        <MetricCard label="Nifty 50 final value" value={formatRs(s.benchmark_final, 0)} change={s.benchmark_return_pct} />
        <MetricCard label="Vs Nifty 50" value={beat === null ? "–" : `${beat > 0 ? "+" : ""}${formatNumber(beat)} points`}
          valueClassName={pnlClass(beat)} help="Total return of the strategy minus Nifty 50's" />
        <MetricCard label="If all sold at the end" value={formatRs(s.liquidation?.value, 0)} help="After exit costs and tax" />
        <MetricCard label="Charges" value={formatRs(s.charges, 0)} help="Brokerage, STT, exchange, SEBI, stamp, GST, DP" />
        <MetricCard label="Slippage" value={formatRs(s.slippage, 0)} help="Worse fill prices than the close" />
        <MetricCard label="Capital gains tax" value={formatRs(s.tax_paid, 0)} />
        <MetricCard label="Rebalances in safe asset" value={`${formatInt(s.days_in_cash)} of ${formatInt(s.rebalance_count)}`}
          help="Nifty 50 was below its average, so nothing was bought" />
      </MetricGrid>
      <DataTable title="Strategy vs Nifty 50" columns={columnsOf(result.comparison, "Measure")} rows={result.comparison}
        searchable={false} downloadName="strategy-vs-nifty" rowKey={(r) => r.Measure} />
      {result.notes?.length > 0 && (
        <ul className="list-disc space-y-1 pl-5 text-xs text-muted-foreground">
          {result.notes.map((n) => <li key={n}>{n}</li>)}
        </ul>
      )}
    </div>
  );
}

// The extra tabs for a momentum portfolio result.
export default function portfolioTabs(result) {
  if (!result) return [];
  return [
    { id: "vs-nifty", label: "Vs Nifty 50", icon: Scale, content: <VsNifty result={result} /> },
    { id: "holdings", label: "Holdings", icon: Briefcase, content: (
      <DataTable columns={HOLDING_COLUMNS} rows={result.holdings} downloadName="holdings" rowKey={(r) => r.symbol}
        emptyText="Nothing held at the end." />) },
    { id: "rebalances", label: `Rebalances (${result.rebalances?.length || 0})`, icon: Repeat, content: (
      <DataTable columns={REBALANCE_COLUMNS} rows={[...(result.rebalances || [])].reverse()} downloadName="rebalances"
        rowKey={(r) => r.trade_day} />) },
    ...(result.tax_by_year?.length ? [{ id: "tax", label: "Tax by year", icon: Receipt, content: (
      <DataTable columns={columnsOf(result.tax_by_year, "Financial year")} rows={result.tax_by_year} searchable={false}
        downloadName="tax-by-year" rowKey={(r) => r["Financial year"]} />) }] : []),
  ];
}
