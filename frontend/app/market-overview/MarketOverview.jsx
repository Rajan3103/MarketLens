"use client";
// Live Market Overview: one symbol's daily candles with the 20 / 50-day moving averages, the buy / sell
// crossovers, the last 10 signals and a download. Data: GET /api/market/history (Yahoo Finance, daily).
// In market hours it refreshes every 60 seconds while the page is visible.
import { useState } from "react";

import DataTable from "@/components/DataTable";
import DownloadButton from "@/components/DownloadButton";
import ErrorBox from "@/components/ErrorBox";
import FormField, { INPUT_CLASS } from "@/components/FormField";
import HelpNote from "@/components/HelpNote";
import HistoricalNote from "@/components/HistoricalNote";
import Loading from "@/components/Loading";
import MetricCard from "@/components/MetricCard";
import PlotlyChart from "@/components/PlotlyChart";
import { apiGet } from "@/lib/api";
import { formatDate, formatRs } from "@/lib/format";
import { isMarketOpen } from "@/lib/market";
import usePolling from "@/lib/usePolling";

const PERIODS = [["6mo", "6 months"], ["1y", "1 year"], ["2y", "2 years"], ["5y", "5 years"]];
const QUICK = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "^NSEI", "^NSEBANK"];
const SIGNAL_COLUMNS = [
  { key: "date", label: "Date", kind: "date" },
  { key: "signal", label: "Signal", kind: "text",
    render: (r) => (r.type === "bullish"
      ? <span className="font-semibold text-green-600 dark:text-green-400">▲ Buy (bullish cross)</span>
      : <span className="font-semibold text-red-600 dark:text-red-400">▼ Sell (bearish cross)</span>) },
  { key: "close", label: "Close", kind: "rs" },
  { key: "ma20", label: "20-day MA", kind: "rs" },
  { key: "ma50", label: "50-day MA", kind: "rs" },
];

// The cards above the chart, from the newest rows.
function Cards({ rows, signals }) {
  const last = rows[rows.length - 1];
  const prev = rows[rows.length - 2];
  const change = last && prev && prev.close ? (100 * (last.close - prev.close)) / prev.close : null;
  const lastSignal = signals[signals.length - 1];
  const trendUp = last && last.ma20 !== null && last.ma50 !== null ? last.ma20 > last.ma50 : null;
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      <MetricCard label={`Close ${formatDate(last?.date)}`} value={formatRs(last?.close)} change={change}
        help="Change from the day before" />
      <MetricCard label="20-day / 50-day MA" value={`${formatRs(last?.ma20, 0)} / ${formatRs(last?.ma50, 0)}`} />
      <MetricCard label="Trend" value={trendUp === null ? "–" : trendUp ? "▲ 20 above 50" : "▼ 20 below 50"} />
      <MetricCard label="Last crossover" value={lastSignal
        ? `${lastSignal.type === "bullish" ? "Buy" : "Sell"} on ${formatDate(lastSignal.date)}` : "None in this period"} />
    </div>
  );
}

export default function MarketOverview() {
  const [draft, setDraft] = useState("RELIANCE.NS");
  const [symbol, setSymbol] = useState("RELIANCE.NS");
  const [period, setPeriod] = useState("1y");
  const [marketOpen] = useState(() => isMarketOpen());
  const query = `market/history?symbol=${encodeURIComponent(symbol)}&period=${period}`;
  const { data, error, loading, refresh } = usePolling(() => apiGet(query), 60,
    { key: query, enabled: marketOpen });

  // Show the typed ticker (Enter or the button).
  const submit = (event) => {
    event.preventDefault();
    const clean = draft.trim().toUpperCase();
    if (clean) setSymbol(clean);
  };
  const shown = data && data.symbol === symbol && data.period === period ? data : null;
  const lastTen = shown ? shown.signals.slice(-10).reverse() : [];

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold">📊 Live Market Overview</h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Daily prices from Yahoo Finance with the 20 and 50-day moving averages and their crossovers.
          {marketOpen ? " Market open: refreshes every 60 seconds." : " Market closed: no auto refresh."}
        </p>
      </div>

      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <FormField label="Ticker" hint="NSE shares end in .NS; indices start with ^" htmlFor="mo-ticker">
          <input id="mo-ticker" value={draft} onChange={(e) => setDraft(e.target.value)} className={`${INPUT_CLASS} w-44`}
            autoComplete="off" spellCheck={false} />
        </FormField>
        <FormField label="Period" htmlFor="mo-period">
          <select id="mo-period" value={period} onChange={(e) => setPeriod(e.target.value)} className={INPUT_CLASS}>
            {PERIODS.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
          </select>
        </FormField>
        <button type="submit" className="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700">
          Show
        </button>
        <div className="flex flex-wrap gap-1 pb-1">
          {QUICK.map((q) => (
            <button key={q} type="button" onClick={() => { setDraft(q); setSymbol(q); }}
              className={`rounded-full border px-2.5 py-0.5 text-xs ${q === symbol ? "border-blue-500 text-blue-700 dark:text-blue-300"
                : "border-slate-300 text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800"}`}>
              {q}
            </button>
          ))}
        </div>
      </form>

      {error && !shown && <ErrorBox error={error} onRetry={refresh} />}
      {!shown && !error && <Loading text={`Loading ${symbol}…`} />}

      {shown && (
        <>
          {loading && <p className="text-xs text-slate-500">Refreshing…</p>}
          <Cards rows={shown.rows} signals={shown.signals} />
          <PlotlyChart figure={shown.chart} height={540} />
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">Last 10 crossover signals</h2>
            <DownloadButton path="market/history/download" params={{ symbol, period }} formats={["csv", "xlsx"]} />
          </div>
          <DataTable columns={SIGNAL_COLUMNS} rows={lastTen} pageSize={10} rowKey={(r) => r.date}
            emptyText="No crossover in this period: the 20-day average stayed on the same side of the 50-day one." />
          <p className="text-xs text-slate-500">
            {shown.count} trading days shown. The download has every day with its prices, both averages and the signal
            (+1 buy, -1 sell, 0 none).
          </p>
        </>
      )}

      <HelpNote title="What are moving averages and crossovers?">
        <p>A <strong>20-day moving average</strong> is the average closing price of the last 20 trading days; it follows
          the price closely. The <strong>50-day</strong> average moves more slowly.</p>
        <p>When the 20-day average crosses <strong>above</strong> the 50-day one (green ▲), recent prices are rising
          faster than the longer trend: a bullish signal. Crossing <strong>below</strong> (red ▼) is bearish.</p>
        <p>Crossovers come late and give false signals in sideways markets, so treat them as a way to study trends,
          not as instructions.</p>
      </HelpNote>
      <HistoricalNote />
    </div>
  );
}
