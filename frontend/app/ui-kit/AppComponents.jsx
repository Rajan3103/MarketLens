"use client";
// The /ui-kit showcase of MarketLens's own components (components/*.jsx) with made-up data from sampleData.js.
// TickerSearch, CostFields and DownloadButton talk to the API, so they need python -m marketlens.api running.
import { Activity, Bell, CandlestickChart, Inbox, Play } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import BacktestResults from "@/components/BacktestResults";
import CodeBlock from "@/components/CodeBlock";
import CostFields from "@/components/CostFields";
import DangerButton from "@/components/DangerButton";
import DataTable from "@/components/DataTable";
import DateRangeField from "@/components/DateRangeField";
import DemoBanner from "@/components/DemoBanner";
import DownloadButton from "@/components/DownloadButton";
import EmptyState from "@/components/EmptyState";
import EquityChart from "@/components/EquityChart";
import ErrorBox from "@/components/ErrorBox";
import FormField from "@/components/FormField";
import HelpNote from "@/components/HelpNote";
import HistoricalNote from "@/components/HistoricalNote";
import Loading from "@/components/Loading";
import MetricCard from "@/components/MetricCard";
import MetricGrid from "@/components/MetricGrid";
import NumberInput, { numberProblem } from "@/components/NumberInput";
import PageHeader from "@/components/PageHeader";
import PlotlyChart from "@/components/PlotlyChart";
import PriceChart from "@/components/PriceChart";
import ProgressBar from "@/components/ProgressBar";
import SelectField from "@/components/SelectField";
import StatusDot from "@/components/StatusDot";
import SwitchField from "@/components/SwitchField";
import Tabs from "@/components/Tabs";
import TickerSearch from "@/components/TickerSearch";
import { Button } from "@/components/ui/button";
import { formatNumber, formatRs } from "@/lib/format";

import { ATM_STRIKE, CANDLES, CHAIN, MARKERS, PINE, RESULT, TRADES, movingAverage } from "./sampleData";

const MA20 = movingAverage(CANDLES, 20);
const MA50 = movingAverage(CANDLES, 50);
const CLOSES = CANDLES.slice(-30).map((c) => c.close);
const CHAIN_COLUMNS = [
  { key: "ce_oi", label: "CE OI", kind: "int" }, { key: "ce_ltp", label: "CE LTP", kind: "number" },
  { key: "strike", label: "Strike", kind: "int" }, { key: "pe_ltp", label: "PE LTP", kind: "number" },
  { key: "pe_oi", label: "PE OI", kind: "int" }, { key: "pcr", label: "PCR", kind: "number" },
];
const TRADE_COLUMNS = [
  { key: "symbol", label: "Symbol", kind: "text" }, { key: "side", label: "Side", kind: "text" },
  { key: "qty", label: "Qty", kind: "int" }, { key: "entry_time", label: "Entry", kind: "date" },
  { key: "exit_reason", label: "Exit", kind: "text" }, { key: "costs", label: "Costs", kind: "rs" },
  { key: "pnl", label: "Net P&L", kind: "rs_signed", colorSign: true },
];
// A small Plotly heatmap like the sector heatmap the API sends.
const HEATMAP = { data: [{ type: "heatmap", x: ["1 day", "1 week", "1 month"], y: ["Bank", "IT", "Auto", "Pharma", "FMCG"],
  z: [[0.8, 2.1, -1.4], [-1.2, -0.4, 3.2], [0.1, 1.6, 4.4], [-0.6, -2.3, -0.9], [0.4, 0.2, 1.1]],
  texttemplate: "%{z:+.1f}%", hovertemplate: "%{y} %{x}: %{z:+.2f}%<extra></extra>" }],
  layout: { title: "Sector change (%)", height: 300, margin: { l: 60, r: 10, t: 50, b: 30 } } };

// One titled block.
function Block({ title, children }) {
  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">{title}</h2>
      {children}
    </section>
  );
}

// The form pieces working together.
function FormDemo() {
  const [capital, setCapital] = useState(500000);
  const [lots, setLots] = useState(2);
  const [underlying, setUnderlying] = useState("NIFTY");
  const [tax, setTax] = useState(true);
  const [range, setRange] = useState({ from: "2025-01-01", to: "2025-12-31" });
  const [ticker, setTicker] = useState(null);
  const [costs, setCosts] = useState({});
  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-3">
        <FormField label="Capital" unit="Rs" htmlFor="kit-capital" help="Money at the start of the backtest"
          error={numberProblem(capital, { min: 10000 })} hint="At least Rs 10,000">
          <NumberInput id="kit-capital" value={capital} onChange={setCapital} min={10000} step={10000} unit="Rs" />
        </FormField>
        <FormField label="Lots" unit="lots" htmlFor="kit-lots" error={numberProblem(lots, { min: 1, max: 50 })}>
          <NumberInput id="kit-lots" value={lots} onChange={setLots} min={1} max={50} />
        </FormField>
        <SelectField id="kit-under" label="Underlying" value={underlying} onChange={setUnderlying}
          options={["NIFTY", "BANKNIFTY", "FINNIFTY", "SENSEX"]} help="The index the options are on" />
        <DateRangeField id="kit-range" label="Backtest period" value={range} onChange={setRange} min="2020-01-01" />
        <FormField label="Symbol" htmlFor="kit-ticker" hint={ticker ? `${ticker.name} · ${ticker.yahoo}` : "Needs the API"}>
          <TickerSearch id="kit-ticker" value={ticker?.symbol} onChange={setTicker} />
        </FormField>
        <SwitchField id="kit-tax" label="Include taxes" checked={tax} onChange={setTax} hint="STT, exchange, SEBI, stamp, GST" />
      </div>
      <CostFields value={costs} onChange={setCosts} />
      <CodeBlock label="What the form would send" code={JSON.stringify({ capital, lots, underlying, include_taxes: tax,
        start: range.from, end: range.to, symbol: ticker?.yahoo ?? null, costs }, null, 2)} />
    </div>
  );
}

// Feedback pieces: progress, loading, errors, notes, buttons.
function FeedbackDemo() {
  return (
    <div className="space-y-4">
      <div className="grid gap-4 md:grid-cols-2">
        <div className="space-y-3 rounded-xl border bg-card p-4">
          <ProgressBar value={35} label="Downloading candles: NIFTY 2025-03" />
          <ProgressBar value={100} label="Done" tone="green" />
          <ProgressBar value={72} label="Daily loss limit used" tone="amber" />
          <ProgressBar value={18} label="Failed: Dhan key not set" tone="red" />
        </div>
        <div className="flex flex-wrap content-start gap-3 rounded-xl border bg-card p-4">
          <StatusDot status="ok" label="Scheduler running" pulse />
          <StatusDot status="warn" label="Dhan token expires today" />
          <StatusDot status="error" label="Database down" />
          <StatusDot status="off" label="Telegram not set" />
        </div>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <Loading text="Loading prices…" variant="cards" />
        <Loading text="Loading the chart…" variant="chart" />
      </div>
      <ErrorBox message="Cannot reach the MarketLens API. Is python -m marketlens.api running?"
        onRetry={() => toast.info("Trying again…")} />
      <DemoBanner demo />
      <HelpNote what="Shows every component with made-up data." how="Switch the theme to check both looks."
        storageKey="ui-kit-demo" />
      <EmptyState icon={Inbox} title="No trades yet" text="Run a backtest and its trades show up here."
        action={<Button size="sm"><Play aria-hidden="true" /> Run backtest</Button>} />
      <div className="flex flex-wrap items-center gap-3">
        <DownloadButton path="market/history/download" params={{ symbol: "^NSEI", period: "1mo" }} formats={["csv", "xlsx"]} />
        <DangerButton label="Kill switch ON" confirmWord="STOP" tick="Every new order is blocked for the rest of the day."
          onConfirm={() => new Promise((done) => setTimeout(done, 600)).then(() => toast.success("Kill switch is ON (demo)"))} />
        <DangerButton label="Delete strategy" tick="The strategy and its saved backtests are deleted."
          onConfirm={() => toast.success("Deleted (demo)")} />
      </div>
      <CodeBlock label="Pine Script v6" code={PINE} />
      <CodeBlock inline code="http://13.200.1.2:8000/webhook/chartink?secret=YOUR_WEBHOOK_SECRET" label="Webhook URL" />
      <HistoricalNote />
    </div>
  );
}

// Everything, in tabs kept in ?kit=.
export default function AppComponents() {
  const last = CANDLES[CANDLES.length - 1];
  const prev = CANDLES[CANDLES.length - 2];
  return (
    <div className="space-y-6">
      <PageHeader icon={CandlestickChart} number={1} title="Live Market Overview"
        description="Index cards, movers, sector heatmap and the price chart (a sample header)."
        actions={<><Button variant="outline" size="sm"><Bell aria-hidden="true" /> Alert</Button><Button size="sm">Refresh</Button></>}
        help={{ what: "Shows the market today.", how: "Green is up, red is down; every arrow also says which way." }} />
      <Tabs param="kit" tabs={[
        { id: "tiles", label: "Tiles", content: (
          <MetricGrid columns={4}>
            <MetricCard label="Nifty 50 (DEMO)" value={formatNumber(last.close)}
              change={((last.close - prev.close) / prev.close) * 100} sparkline={CLOSES} help="Last close and day change" />
            <MetricCard label="India VIX" value="14.32" change={6.1} invert help="A rise in VIX is bad news" />
            <MetricCard label="Today's P&L" value={formatRs(4875.5, 0, true)} change={4875.5} changeKind="rs" valueClassName="text-profit" />
            <MetricCard label="Loading" loading />
          </MetricGrid>) },
        { id: "charts", label: "Charts", icon: Activity, content: (
          <div className="space-y-4">
            <PriceChart title="NIFTY (DEMO) daily" candles={CANDLES} markers={MARKERS}
              overlays={[{ name: "MA 20", data: MA20 }, { name: "MA 50", data: MA50 }]} />
            <PriceChart title="Line mode" mode="line" candles={CANDLES} height={260} showVolume={false} />
            <EquityChart curves={[{ name: "Strategy", points: RESULT.equity }, ...RESULT.benchmarks]} drawdown={RESULT.drawdown} />
            <PlotlyChart figure={HEATMAP} />
          </div>) },
        { id: "tables", label: "Tables", content: (
          <div className="space-y-6">
            <DataTable title="Option chain (ATM highlighted)" columns={CHAIN_COLUMNS} rows={CHAIN} searchable={false}
              rowKey={(r) => r.strike} highlightRow={(r) => r.strike === ATM_STRIKE} downloadName="chain" />
            <DataTable title="Trades" columns={TRADE_COLUMNS} rows={TRADES} initialSort={{ key: "entry_time", dir: "desc" }}
              rowKey={(r, i) => `${r.symbol}-${i}`} downloadName="trades"
              rowActions={[{ label: "Open", onClick: (r) => toast.info(`Open ${r.symbol}`) }]} />
          </div>) },
        { id: "forms", label: "Forms", content: <FormDemo /> },
        { id: "feedback", label: "Feedback", content: <FeedbackDemo /> },
        { id: "backtest", label: "Backtest results", content: <BacktestResults result={RESULT} /> },
      ]} />
    </div>
  );
}
