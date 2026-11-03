"use client";
// Price chart for any ticker: candles with the fast / slow moving averages and their crossovers, optional
// 200-day MA and Bollinger Bands, RSI(14) and volume panels, every past crossover and a download.
// Data: GET /api/market/history (Yahoo Finance, daily). In market hours it refreshes every 60 seconds.
import { useState } from "react";

import DataTable from "@/components/DataTable";
import DownloadButton from "@/components/DownloadButton";
import ErrorBox from "@/components/ErrorBox";
import FormField, { INPUT_CLASS } from "@/components/FormField";
import Loading from "@/components/Loading";
import MetricCard from "@/components/MetricCard";
import PlotlyChart from "@/components/PlotlyChart";
import { apiGet } from "@/lib/api";
import { formatDate, formatNumber, formatRs } from "@/lib/format";
import usePolling from "@/lib/usePolling";

const PERIODS = [["6mo", "6 months"], ["1y", "1 year"], ["2y", "2 years"], ["5y", "5 years"]];
const QUICK = ["RELIANCE.NS", "TCS.NS", "HDFCBANK.NS", "^NSEI", "^NSEBANK"];
const MIN_DAYS = 5;
const MAX_DAYS = 200;

// The signal table's columns; the average titles follow the chosen days.
function signalColumns(fast, slow) {
  return [
    { key: "date", label: "Date", kind: "date" },
    { key: "type", label: "Signal", kind: "text",
      render: (r) => (r.type === "bullish"
        ? <span className="font-semibold text-green-600 dark:text-green-400">▲ Bullish cross (buy)</span>
        : <span className="font-semibold text-red-600 dark:text-red-400">▼ Bearish cross (sell)</span>) },
    { key: "close", label: "Close", kind: "rs" },
    { key: "ma_fast", label: `${fast}-day MA`, kind: "rs" },
    { key: "ma_slow", label: `${slow}-day MA`, kind: "rs" },
  ];
}

// A problem with the typed average lengths, or "" when they are fine.
function daysProblem(fast, slow) {
  const ok = (n) => Number.isInteger(n) && n >= MIN_DAYS && n <= MAX_DAYS;
  if (!ok(fast) || !ok(slow)) return `Averages must be whole days from ${MIN_DAYS} to ${MAX_DAYS}.`;
  if (fast >= slow) return "The fast average must be shorter than the slow one.";
  return "";
}

// The cards above the chart, from the newest rows.
function Cards({ data }) {
  const { rows, signals, fast, slow } = data;
  const last = rows[rows.length - 1];
  const prev = rows[rows.length - 2];
  const change = last && prev && prev.close ? (100 * (last.close - prev.close)) / prev.close : null;
  const lastSignal = signals[signals.length - 1];
  const trendUp = last && last.ma_fast !== null && last.ma_slow !== null ? last.ma_fast > last.ma_slow : null;
  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
      <MetricCard label={`Close ${formatDate(last?.date)}`} value={formatRs(last?.close)} change={change}
        help="Change from the day before" />
      <MetricCard label={`${fast}-day / ${slow}-day MA`} value={`${formatRs(last?.ma_fast, 0)} / ${formatRs(last?.ma_slow, 0)}`} />
      <MetricCard label="Trend" value={trendUp === null ? "–" : trendUp ? `▲ ${fast} above ${slow}` : `▼ ${fast} below ${slow}`} />
      <MetricCard label="RSI (14)" value={formatNumber(last?.rsi, 1)}
        help="Above 70 is often called overbought, below 30 oversold" />
      <MetricCard label="Last crossover" value={lastSignal
        ? `${lastSignal.type === "bullish" ? "Buy" : "Sell"} on ${formatDate(lastSignal.date)}` : "None in this period"} />
    </div>
  );
}

export default function PriceChart({ marketOpen }) {
  const [draft, setDraft] = useState({ symbol: "RELIANCE.NS", fast: "20", slow: "50" });
  const [chosen, setChosen] = useState({ symbol: "RELIANCE.NS", fast: 20, slow: 50 });
  const [period, setPeriod] = useState("1y");
  const [ma200, setMa200] = useState(false);
  const [bollinger, setBollinger] = useState(false);
  const [problem, setProblem] = useState("");

  const { symbol, fast, slow } = chosen;
  const params = { symbol, period, fast, slow };
  const query = `market/history?${new URLSearchParams({ ...params, ma200, bollinger })}`;
  const { data, error, loading, refresh } = usePolling(() => apiGet(query), 60, { key: query, enabled: marketOpen });

  // Show the typed ticker and averages (Enter or the Show button).
  const submit = (event) => {
    event.preventDefault();
    const next = { symbol: draft.symbol.trim().toUpperCase(), fast: Number(draft.fast), slow: Number(draft.slow) };
    const why = !next.symbol ? "Type a ticker, e.g. RELIANCE.NS." : daysProblem(next.fast, next.slow);
    setProblem(why);
    if (!why) setChosen(next);
  };
  // A quick ticker button: show it at once with the averages already chosen.
  const pick = (q) => {
    setDraft((d) => ({ ...d, symbol: q }));
    setChosen((c) => ({ ...c, symbol: q }));
  };

  const shown = data && data.symbol === symbol && data.period === period && data.fast === fast && data.slow === slow
    ? data : null;
  const signalsNewestFirst = shown ? [...shown.signals].reverse() : [];

  return (
    <section className="space-y-4">
      <h2 className="text-lg font-semibold">Price chart with indicators</h2>
      <form onSubmit={submit} className="flex flex-wrap items-end gap-3">
        <FormField label="Ticker" hint="NSE shares end in .NS; indices start with ^" htmlFor="mo-ticker">
          <input id="mo-ticker" value={draft.symbol} onChange={(e) => setDraft({ ...draft, symbol: e.target.value })}
            className={`${INPUT_CLASS} w-44`} autoComplete="off" spellCheck={false} />
        </FormField>
        <FormField label="Timeframe" htmlFor="mo-period">
          <select id="mo-period" value={period} onChange={(e) => setPeriod(e.target.value)} className={INPUT_CLASS}>
            {PERIODS.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
          </select>
        </FormField>
        <FormField label="Fast average" unit="days" htmlFor="mo-fast">
          <input id="mo-fast" type="number" min={MIN_DAYS} max={MAX_DAYS} value={draft.fast}
            onChange={(e) => setDraft({ ...draft, fast: e.target.value })} className={`${INPUT_CLASS} w-24`} />
        </FormField>
        <FormField label="Slow average" unit="days" htmlFor="mo-slow">
          <input id="mo-slow" type="number" min={MIN_DAYS} max={MAX_DAYS} value={draft.slow}
            onChange={(e) => setDraft({ ...draft, slow: e.target.value })} className={`${INPUT_CLASS} w-24`} />
        </FormField>
        <button type="submit" className="rounded-md bg-blue-600 px-4 py-2 text-sm font-semibold text-white hover:bg-blue-700">
          Show
        </button>
      </form>
      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
        <div className="flex flex-wrap gap-1">
          {QUICK.map((q) => (
            <button key={q} type="button" onClick={() => pick(q)}
              className={`rounded-full border px-2.5 py-0.5 text-xs ${q === symbol ? "border-blue-500 text-blue-700 dark:text-blue-300"
                : "border-slate-300 text-slate-600 hover:bg-slate-100 dark:border-slate-700 dark:text-slate-400 dark:hover:bg-slate-800"}`}>
              {q}
            </button>
          ))}
        </div>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={ma200} onChange={(e) => setMa200(e.target.checked)} /> 200-day MA
        </label>
        <label className="flex items-center gap-2">
          <input type="checkbox" checked={bollinger} onChange={(e) => setBollinger(e.target.checked)} />
          Bollinger Bands (20, 2)
        </label>
      </div>
      {problem && <ErrorBox message={problem} />}

      {error && !shown && <ErrorBox error={error} onRetry={refresh} />}
      {!shown && !error && <Loading text={`Loading ${symbol}…`} />}

      {shown && (
        <>
          {loading && <p className="text-xs text-slate-500">Refreshing…</p>}
          <Cards data={shown} />
          <PlotlyChart figure={shown.chart} height={800} />
          {shown.no_volume && (
            <p className="text-xs text-slate-500">This ticker has no volume data on yfinance (indices usually don&apos;t).</p>
          )}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <h2 className="text-lg font-semibold">Past crossover signals ({shown.signals.length})</h2>
            <DownloadButton path="market/history/download" params={params} formats={["csv", "xlsx"]} />
          </div>
          <DataTable columns={signalColumns(fast, slow)} rows={signalsNewestFirst} pageSize={10} rowKey={(r) => r.date}
            emptyText={`No crossover in this period: the ${fast}-day average stayed on the same side of the ${slow}-day one.`} />
          <p className="text-xs text-slate-500">
            {shown.count} trading days shown. The download has every day with its prices, both averages, the 200-day
            average, RSI 14, the Bollinger Bands and the signal (+1 buy, -1 sell, 0 none).
          </p>
        </>
      )}
    </section>
  );
}
