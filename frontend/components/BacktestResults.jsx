"use client";
// The results of any backtest in the shared shape (result_to_json, marketlens/backtest/present.py), in tabs:
// Summary (tiles), Equity & drawdown (EquityChart), Calendar (heatmaps), Trades (table) and Yearly. A page can add
// its own tabs after these (e.g. the momentum engine's holdings and rebalances).
//
//   <BacktestResults result={job.result} extraTabs={[{ id: "holdings", label: "Holdings", content: <…/> }]} />
//
// The open tab is kept in the address as ?result=… (param), so it never clashes with the page's own ?tab=.
import { CalendarDays, LayoutGrid, LineChart, ListOrdered, TableProperties } from "lucide-react";

import DataTable from "@/components/DataTable";
import DemoBanner from "@/components/DemoBanner";
import EmptyState from "@/components/EmptyState";
import EquityChart from "@/components/EquityChart";
import HistoricalNote from "@/components/HistoricalNote";
import MetricCard from "@/components/MetricCard";
import MetricGrid from "@/components/MetricGrid";
import PlotlyChart from "@/components/PlotlyChart";
import Tabs from "@/components/Tabs";
import { formatDate, formatInt, formatNumber, formatPct, formatRs, isEmpty, pnlClass } from "@/lib/format";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
// Metrics that are a profit or a loss: shown with a sign and coloured.
const SIGNED = new Set(["total_pnl", "return_pct", "cagr_pct", "expectancy", "avg_win", "avg_loss", "best_day",
  "worst_day", "max_drawdown_rs", "max_drawdown_pct"]);
// The trade list (the columns every backtester writes, see backtest/engine.py TRADE_COLUMNS).
const TRADE_COLUMNS = [
  { key: "leg_no", label: "Leg", kind: "int", hidden: true },
  { key: "symbol", label: "Symbol", kind: "text" },
  { key: "side", label: "Side", kind: "text" },
  { key: "qty", label: "Qty", kind: "int" },
  { key: "entry_time", label: "Entry", kind: "date" },
  { key: "entry_price", label: "Entry price", kind: "number" },
  { key: "exit_time", label: "Exit", kind: "date" },
  { key: "exit_price", label: "Exit price", kind: "number" },
  { key: "exit_reason", label: "Exit reason", kind: "text" },
  { key: "note", label: "Note", kind: "text", hidden: true },
  { key: "gross_pnl", label: "Gross P&L", kind: "rs", colorSign: true },
  { key: "costs", label: "Costs", kind: "rs" },
  { key: "pnl", label: "Net P&L", kind: "rs", colorSign: true },
];
const YEAR_COLUMNS = [
  { key: "year", label: "Year", kind: "text" },
  { key: "pnl", label: "P&L", kind: "rs_signed", colorSign: true, decimals: 0 },
  { key: "return_pct", label: "Return", kind: "pct_signed", colorSign: true },
];
const MONTH_COLUMNS = [
  { key: "year", label: "Year", kind: "text" },
  { key: "month_name", label: "Month", kind: "text" },
  { key: "pnl", label: "P&L", kind: "rs_signed", colorSign: true, decimals: 0 },
  { key: "return_pct", label: "Return", kind: "pct_signed", colorSign: true },
];
const SKIPPED_COLUMNS = [{ key: "date", label: "Day", kind: "date" }, { key: "reason", label: "Why it was skipped", kind: "text" }];

// One metric as text, by its unit (Rs, %, ratio, trades, days).
export function metricText(m) {
  if (isEmpty(m.value)) return "–";
  const signed = SIGNED.has(m.key);
  if (m.unit === "Rs") return formatRs(m.value, 0, signed);
  if (m.unit === "%") return formatPct(m.value, 2, signed);
  if (m.unit === "ratio") return formatNumber(m.value, 2);
  return formatInt(m.value);
}

// Summary: every metric as a tile.
function Summary({ result }) {
  return (
    <div className="space-y-4">
      <MetricGrid columns={4}>
        {result.metrics.map((m) => (
          <MetricCard key={m.key} label={m.label} value={metricText(m)} help={m.help}
            valueClassName={SIGNED.has(m.key) ? pnlClass(m.value) : ""} />
        ))}
      </MetricGrid>
      {result.skipped_days?.length > 0 && (
        <p className="text-sm text-muted-foreground">
          {formatInt(result.skipped_days.length)} day(s) were skipped; the Trades tab lists them with the reason.
        </p>
      )}
    </div>
  );
}

// Equity & drawdown: the strategy and every benchmark on one chart, the drawdown underneath.
function Equity({ result }) {
  const curves = [{ name: "Strategy", points: result.equity }, ...(result.benchmarks || [])];
  return <EquityChart curves={curves} drawdown={result.drawdown} />;
}

// Calendar: the day-by-day and month-by-month heatmaps.
function Calendar({ result }) {
  if (!result.day_heatmap && !result.month_heatmap) {
    return <EmptyState icon={CalendarDays} title="No trades, so no calendar" />;
  }
  return (
    <div className="space-y-4">
      <PlotlyChart figure={result.day_heatmap} />
      <PlotlyChart figure={result.month_heatmap} />
    </div>
  );
}

// Trades: every trade, then the skipped days.
function Trades({ result }) {
  const shown = result.trades?.length || 0;
  return (
    <div className="space-y-6">
      {result.trades_total > shown && (
        <p className="text-sm text-muted-foreground">
          The newest {formatInt(shown)} of {formatInt(result.trades_total)} trades are shown; the Excel report has all.
        </p>
      )}
      <DataTable columns={TRADE_COLUMNS} rows={result.trades} downloadName="trades" emptyText="No trades in this period."
        rowKey={(r, i) => `${r.symbol}-${r.entry_time}-${r.exit_time}-${i}`} />
      {result.skipped_days?.length > 0 && (
        <DataTable title="Skipped days" columns={SKIPPED_COLUMNS} rows={result.skipped_days} downloadName="skipped-days"
          rowKey={(r, i) => `${r.date}-${i}`} />
      )}
    </div>
  );
}

// Yearly: P&L and return per year, then per month.
function Yearly({ result }) {
  const months = (result.monthly_returns || []).map((m) => ({ ...m, month_name: MONTHS[m.month - 1] }));
  return (
    <div className="grid gap-6 lg:grid-cols-2">
      <DataTable title="Year by year" columns={YEAR_COLUMNS} rows={result.yearly_returns} searchable={false}
        downloadName="yearly-returns" rowKey={(r) => r.year} emptyText="No yearly returns yet." />
      <DataTable title="Month by month" columns={MONTH_COLUMNS} rows={months} downloadName="monthly-returns"
        rowKey={(r) => `${r.year}-${r.month}`} emptyText="No monthly returns yet." />
    </div>
  );
}

export default function BacktestResults({ result, extraTabs = [], param = "result" }) {
  if (!result) return null;
  const tabs = [
    { id: "summary", label: "Summary", icon: LayoutGrid, content: <Summary result={result} /> },
    { id: "equity", label: "Equity & drawdown", icon: LineChart, content: <Equity result={result} /> },
    { id: "calendar", label: "Calendar", icon: CalendarDays, content: <Calendar result={result} /> },
    { id: "trades", label: "Trades", icon: ListOrdered, content: <Trades result={result} /> },
    { id: "yearly", label: "Yearly", icon: TableProperties, content: <Yearly result={result} /> },
    ...extraTabs,
  ];
  return (
    <section className="space-y-4" aria-label="Backtest results">
      {result.is_demo && <DemoBanner demo />}
      <div>
        <h2 className="text-lg font-semibold">{result.name}</h2>
        <p className="num text-sm text-muted-foreground">
          {formatDate(result.start)} to {formatDate(result.end)} · starting capital {formatRs(result.capital, 0)}
          {result.run_id ? ` · saved as run ${result.run_id}` : ""} · all P&amp;L after costs
        </p>
      </div>
      <Tabs tabs={tabs} param={param} />
      {result.historical !== false && <HistoricalNote />}
    </section>
  );
}
