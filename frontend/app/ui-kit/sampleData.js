// Made-up market data for the /ui-kit page only: candles, moving averages, trades and a whole backtest result
// in the result_to_json shape. A fixed seed means the page looks the same every time.

// A repeatable random number generator (mulberry32): the same seed gives the same numbers.
function randomFrom(seed) {
  let a = seed;
  return () => {
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

// Weekdays from a start date as "YYYY-MM-DD".
function weekdays(start, count) {
  const out = [];
  const day = new Date(`${start}T00:00:00Z`);
  while (out.length < count) {
    if (day.getUTCDay() % 6 !== 0) out.push(day.toISOString().slice(0, 10));
    day.setUTCDate(day.getUTCDate() + 1);
  }
  return out;
}

const round = (n) => Math.round(n * 100) / 100;

// 300 daily candles for a made-up index, with volume.
export const CANDLES = (() => {
  const rnd = randomFrom(7);
  let close = 24000;
  return weekdays("2025-09-01", 300).map((time) => {
    const open = close * (1 + (rnd() - 0.5) * 0.004);
    close = open * (1 + (rnd() - 0.48) * 0.02);
    const high = Math.max(open, close) * (1 + rnd() * 0.006);
    const low = Math.min(open, close) * (1 - rnd() * 0.006);
    return { time, open: round(open), high: round(high), low: round(low), close: round(close),
      volume: Math.round(150_000 + rnd() * 250_000) };
  });
})();

// A simple moving average of the closes as [{ time, value }] (empty until there are `days` closes).
export function movingAverage(candles, days) {
  return candles.map((c, i) => {
    if (i < days - 1) return { time: c.time, value: null };
    const window = candles.slice(i - days + 1, i + 1);
    return { time: c.time, value: round(window.reduce((sum, w) => sum + w.close, 0) / days) };
  });
}

// Buy / sell markers where the 20-day average crosses the 50-day one.
export const MARKERS = (() => {
  const fast = movingAverage(CANDLES, 20);
  const slow = movingAverage(CANDLES, 50);
  const out = [];
  for (let i = 50; i < CANDLES.length; i += 1) {
    const before = fast[i - 1].value - slow[i - 1].value;
    const now = fast[i].value - slow[i].value;
    if (before <= 0 && now > 0) out.push({ time: CANDLES[i].time, side: "BUY", text: "MA cross up" });
    if (before >= 0 && now < 0) out.push({ time: CANDLES[i].time, side: "SELL", text: "MA cross down" });
  }
  return out;
})();

// 80 made-up trades for the tables.
export const TRADES = (() => {
  const rnd = randomFrom(11);
  const days = weekdays("2025-01-01", 80);
  return days.map((day, i) => {
    const side = rnd() > 0.5 ? "SELL" : "BUY";
    const entry = round(80 + rnd() * 60);
    const exit = round(Math.max(entry * (1 + (rnd() - 0.48) * 0.6), 0.05));
    const qty = 65 * (1 + Math.floor(rnd() * 3));
    const gross = round((side === "BUY" ? exit - entry : entry - exit) * qty);
    const costs = round(40 + rnd() * 30);
    return { leg_no: (i % 2) + 1, symbol: `NIFTY ${24000 + 50 * Math.floor(rnd() * 20)} ${i % 2 ? "PE" : "CE"}`, side,
      qty, entry_time: `${day}T09:20:00+05:30`, entry_price: entry, exit_time: `${day}T15:15:00+05:30`,
      exit_price: exit, exit_reason: ["TIME", "SL", "TARGET", "TRAIL_SL"][Math.floor(rnd() * 4)], note: null,
      gross_pnl: gross, costs, pnl: round(gross - costs) };
  });
})();

// An option chain around 24,550 (the ATM row is highlighted in the table).
export const CHAIN = Array.from({ length: 11 }, (_, i) => {
  const strike = 24300 + 50 * i;
  return { strike, ce_ltp: round(Math.max(24550 - strike, 0) + 60 + i * 2), ce_oi: 120000 + i * 9000,
    pe_ltp: round(Math.max(strike - 24550, 0) + 55 + (10 - i) * 2), pe_oi: 210000 - i * 11000,
    pcr: round((210000 - i * 11000) / (120000 + i * 9000)) };
});
export const ATM_STRIKE = 24550;

// A whole backtest result in the result_to_json shape (marketlens/backtest/present.py).
export const RESULT = (() => {
  const rnd = randomFrom(3);
  const capital = 500_000;
  const days = weekdays("2025-01-01", 260);
  let value = capital;
  let nifty = capital;
  let high = capital;
  const equity = [];
  const bench = [];
  const drawdown = [];
  const daily = [];
  days.forEach((time) => {
    const pnl = round((rnd() - 0.46) * 4000);
    value += pnl;
    nifty *= 1 + (rnd() - 0.48) * 0.012;
    high = Math.max(high, value);
    equity.push({ time, value: round(value) });
    bench.push({ time, value: round(nifty) });
    drawdown.push({ time, value: round(((value - high) / high) * 100), rs: round(value - high) });
    daily.push({ time, pnl });
  });
  const months = {};
  daily.forEach(({ time, pnl }) => { const key = time.slice(0, 7); months[key] = (months[key] || 0) + pnl; });
  let start = capital;
  const monthly = Object.entries(months).map(([key, pnl]) => {
    const row = { year: Number(key.slice(0, 4)), month: Number(key.slice(5, 7)), pnl: round(pnl),
      return_pct: round((pnl / start) * 100) };
    start += pnl;
    return row;
  });
  const total = round(value - capital);
  const heat = (title, x, y, z) => ({ data: [{ type: "heatmap", x, y, z, zmid: 0, xgap: 2, ygap: 2,
    hovertemplate: "%{x} %{y}<br>P&L Rs %{z:,.0f}<extra></extra>" }], layout: { title, height: 260,
    margin: { l: 40, r: 10, t: 50, b: 30 } } });
  const monthNames = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const metric = (key, label, val, unit, help) => ({ key, label, value: val, unit, help });
  return {
    name: "DEMO: NIFTY short straddle 09:20-15:15", start: days[0], end: days[days.length - 1], capital,
    is_demo: true, params: { underlying: "NIFTY", lots: 1 },
    metrics: [
      metric("total_pnl", "Total P&L", total, "Rs", "Profit or loss after brokerage, taxes and slippage"),
      metric("return_pct", "Return on capital", round((total / capital) * 100), "%", "Total P&L as a percent of capital"),
      metric("cagr_pct", "CAGR", 9.8, "%", "Yearly growth rate if returns compound"),
      metric("max_drawdown_rs", "Max drawdown", Math.min(...drawdown.map((d) => d.rs)), "Rs", "Biggest fall from a high"),
      metric("max_drawdown_pct", "Max drawdown %", Math.min(...drawdown.map((d) => d.value)), "%", "The same fall in %"),
      metric("win_rate_pct", "Win rate", 56.2, "%", "Trades that made money"),
      metric("trades", "Trades", TRADES.length, "trades", "Number of closed trades"),
      metric("profit_factor", "Profit factor", 1.34, "ratio", "Money won / money lost; above 1 is profitable"),
      metric("sharpe", "Sharpe", 1.12, "ratio", "Return per unit of risk (yearly). Above 1 is good"),
      metric("sortino", "Sortino", 1.61, "ratio", "Like Sharpe, but only losing days count as risk"),
      metric("calmar", "Calmar", 0.92, "ratio", "CAGR divided by the max drawdown"),
      metric("expectancy", "Expectancy", 812.5, "Rs", "Average P&L per trade after costs"),
    ],
    equity: [{ time: days[0], value: capital }, ...equity.slice(1)],
    drawdown: drawdown.map(({ time, value: v, rs }) => ({ time, value: v, rs })),
    benchmark_name: "NIFTY (DEMO)", benchmark: bench,
    benchmarks: [{ name: "NIFTY (DEMO)", points: bench }],
    yearly_returns: [{ year: 2025, pnl: round(monthly.filter((m) => m.year === 2025).reduce((s, m) => s + m.pnl, 0)),
      return_pct: 4.1 }, { year: 2026, pnl: round(monthly.filter((m) => m.year === 2026).reduce((s, m) => s + m.pnl, 0)),
      return_pct: -1.3 }],
    monthly_returns: monthly,
    day_heatmap: heat("Day-wise P&L (Rs)", days.slice(0, 60).filter((_, i) => i % 5 === 0), ["Mon", "Tue", "Wed", "Thu", "Fri"],
      ["Mon", "Tue", "Wed", "Thu", "Fri"].map((_, r) => Array.from({ length: 12 }, (_, c) => daily[c * 5 + r]?.pnl ?? null))),
    month_heatmap: heat("Month-wise P&L (Rs)", monthNames, ["2025", "2026"],
      [2025, 2026].map((y) => monthNames.map((_, m) => monthly.find((r) => r.year === y && r.month === m + 1)?.pnl ?? null))),
    trades: TRADES, trades_total: TRADES.length,
    skipped_days: [{ date: "2025-03-14", reason: "NSE holiday: Holi" }, { date: "2025-08-15", reason: "Independence Day" }],
    run_id: null, historical: true, note: "Historical analysis for learning, not a live trading recommendation.",
  };
})();

export const PINE = `//@version=6
strategy("MarketLens: MA 20 / 50 cross", overlay = true)
fast = ta.sma(close, 20)
slow = ta.sma(close, 50)
if ta.crossover(fast, slow)
    strategy.entry("Long", strategy.long)
if ta.crossunder(fast, slow)
    strategy.close("Long")
plot(fast, "MA 20", color.blue)
plot(slow, "MA 50", color.orange)`;
