"use client";
// The results of a portfolio backtest (from marketlens/api/results.py): cards, strategy vs Nifty table,
// value and drawdown charts, yearly returns, final holdings, every rebalance and the trade list.
//
//   <BacktestResults result={job.result} />
import DataTable from "@/components/DataTable";
import DemoBanner from "@/components/DemoBanner";
import MetricCard from "@/components/MetricCard";
import PlotlyChart from "@/components/PlotlyChart";
import { formatDate, formatInt, formatNumber, formatPct, formatRs, isEmpty } from "@/lib/format";

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
const TRADE_COLUMNS = [
  { key: "symbol", label: "Stock", kind: "text" },
  { key: "qty", label: "Shares", kind: "int" },
  { key: "entry_time", label: "Bought", kind: "date" },
  { key: "entry_price", label: "Buy price", kind: "rs" },
  { key: "exit_time", label: "Sold", kind: "date" },
  { key: "exit_price", label: "Sell price", kind: "rs" },
  { key: "exit_reason", label: "Why", kind: "text" },
  { key: "note", label: "Gain type", kind: "text" },
  { key: "costs", label: "Costs", kind: "rs" },
  { key: "pnl", label: "P&L after costs", kind: "rs", colorSign: true },
];

// Columns of a table whose first column is text and the rest are numbers (comparison, yearly, tax).
function columnsOf(rows, first, signed = false) {
  if (!rows?.length) return [];
  return Object.keys(rows[0]).map((key) => (key === first
    ? { key, label: key, kind: "text" }
    : { key, label: key, kind: "number", colorSign: signed }));
}

// A small heading.
function Heading({ children }) {
  return <h3 className="text-base font-semibold">{children}</h3>;
}

export default function BacktestResults({ result }) {
  if (!result) return null;
  const s = result.summary;
  const beat = isEmpty(s.benchmark_return_pct) ? null : s.total_return_pct - s.benchmark_return_pct;
  return (
    <div className="space-y-6">
      <DemoBanner demo={result.is_demo} />
      <div>
        <h2 className="text-lg font-semibold">{result.name}</h2>
        <p className="text-sm text-slate-500">
          {formatDate(result.start)} to {formatDate(result.end)} · starting capital {formatRs(result.capital, 0)}
          {result.run_id ? ` · saved as run ${result.run_id}` : ""}
        </p>
      </div>

      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        <MetricCard label="Final value" value={formatRs(s.final_value, 0)} change={s.total_return_pct} />
        <MetricCard label="CAGR" value={formatPct(s.cagr_pct)} help="Yearly growth rate over the whole period" />
        <MetricCard label="Max drawdown" value={formatPct(s.max_drawdown_pct)}
          help="Biggest fall from a high, in % of that high" />
        <MetricCard label="Sharpe" value={formatNumber(s.sharpe)} help="Return per unit of risk (daily, yearly)" />
        <MetricCard label="Nifty 50 final value" value={formatRs(s.benchmark_final, 0)} change={s.benchmark_return_pct} />
        <MetricCard label="Vs Nifty 50" value={beat === null ? "–" : `${beat > 0 ? "+" : ""}${formatNumber(beat)} points`}
          change={beat} changeText={beat === null ? null : beat >= 0 ? "ahead" : "behind"}
          help="Total return of the strategy minus Nifty 50's" />
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        <MetricCard label="Charges" value={formatRs(s.charges, 0)} help="Brokerage, STT, exchange, SEBI, stamp, GST, DP" />
        <MetricCard label="Slippage" value={formatRs(s.slippage, 0)} help="Worse fill prices than the close" />
        <MetricCard label="Capital gains tax" value={formatRs(s.tax_paid, 0)} />
        <MetricCard label="Orders" value={formatInt(s.orders)} help={`${s.rebalance_count} rebalances`} />
        <MetricCard label="If all sold at the end" value={formatRs(s.liquidation?.value, 0)}
          help="After exit costs and tax" />
        <MetricCard label="Rebalances in safe asset" value={`${formatInt(s.days_in_cash)} of ${formatInt(s.rebalance_count)}`}
          help="Nifty 50 was below its average, so nothing was bought" />
      </div>

      <PlotlyChart figure={result.equity_chart} height={420} />
      <PlotlyChart figure={result.drawdown_chart} height={280} />

      <div className="grid gap-6 lg:grid-cols-2">
        <div className="space-y-2">
          <Heading>Strategy vs Nifty 50</Heading>
          <DataTable columns={columnsOf(result.comparison, "Measure")} rows={result.comparison} pageSize={20}
            rowKey={(r) => r.Measure} />
        </div>
        <div className="space-y-2">
          <Heading>Year by year (%)</Heading>
          <DataTable columns={columnsOf(result.yearly, "Year", true)} rows={result.yearly} pageSize={20}
            rowKey={(r) => r.Year} />
        </div>
      </div>

      <div className="space-y-2">
        <Heading>Holdings at the end</Heading>
        <DataTable columns={HOLDING_COLUMNS} rows={result.holdings} pageSize={25} rowKey={(r) => r.symbol} />
      </div>

      <details className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
        <summary className="cursor-pointer font-semibold">Every rebalance ({result.rebalances.length})</summary>
        <div className="pt-3">
          <DataTable columns={REBALANCE_COLUMNS} rows={[...result.rebalances].reverse()} pageSize={15}
            rowKey={(r) => r.trade_day} />
        </div>
      </details>
      <details className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
        <summary className="cursor-pointer font-semibold">
          Trades ({result.trades_total}{result.trades_total > result.trades.length ? `, newest ${result.trades.length} shown` : ""})
        </summary>
        <div className="pt-3">
          <DataTable columns={TRADE_COLUMNS} rows={result.trades} pageSize={20}
            rowKey={(r, i) => `${r.symbol}-${r.entry_time}-${r.exit_time}-${i}`} />
        </div>
      </details>
      {result.tax_by_year?.length > 0 && (
        <details className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
          <summary className="cursor-pointer font-semibold">Capital gains tax by financial year</summary>
          <div className="pt-3">
            <DataTable columns={columnsOf(result.tax_by_year, "Financial year")} rows={result.tax_by_year}
              rowKey={(r) => r["Financial year"]} />
          </div>
        </details>
      )}
      <ul className="list-disc space-y-1 pl-5 text-xs text-slate-500">
        {result.notes.map((n) => <li key={n}>{n}</li>)}
      </ul>
    </div>
  );
}
