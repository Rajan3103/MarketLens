"use client";
// The backtest settings form. value = StockMomentumSettings as plain values (GET /api/momentum/options);
// onChange(next) gets the whole new object. Every field shows its unit.
import FormField, { INPUT_CLASS } from "@/components/FormField";

const LOOKBACKS = [[21, "1 month"], [63, "3 months"], [126, "6 months"], [252, "12 months"]];
const METHOD_TEXT = { return: "% return", vol_adjusted: "return ÷ volatility", sharpe: "Sharpe-style" };

// A number box that keeps what you type and hands back a number (empty -> 0).
function NumberInput({ id, value, onChange, min, max, step = 1, width = "w-28" }) {
  return (
    <input id={id} type="number" min={min} max={max} step={step} value={value ?? ""} className={`${INPUT_CLASS} ${width}`}
      onChange={(e) => onChange(e.target.value === "" ? 0 : Number(e.target.value))} />
  );
}

// A tick box with its words.
function Check({ checked, onChange, children }) {
  return (
    <label className="flex items-center gap-2 text-sm">
      <input type="checkbox" checked={Boolean(checked)} onChange={(e) => onChange(e.target.checked)} />
      {children}
    </label>
  );
}

// A grey box around a group of fields.
function Group({ title, children, open = true }) {
  return (
    <details open={open} className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
      <summary className="cursor-pointer font-semibold">{title}</summary>
      <div className="flex flex-wrap items-start gap-4 pt-3">{children}</div>
    </details>
  );
}

export default function SettingsForm({ value, onChange, options }) {
  const v = value;
  const f = v.filters;
  const set = (key, next) => onChange({ ...v, [key]: next });
  const setFilter = (key, next) => onChange({ ...v, filters: { ...f, [key]: next } });
  const setCost = (key, next) => onChange({ ...v, costs: { ...v.costs, [key]: next } });
  const setTax = (key, next) => onChange({ ...v, tax: { ...v.tax, [key]: next } });
  const size = v.universe === "nifty50" ? 50 : 200;

  // Tick or untick one lookback; every chosen lookback counts the same.
  const toggleLookback = (days, on) => {
    const chosen = LOOKBACKS.map(([d]) => d).filter((d) => (d === days ? on : f.lookbacks.includes(d)));
    if (!chosen.length) return;
    onChange({ ...v, filters: { ...f, lookbacks: chosen, weights: chosen.map(() => 1) } });
  };

  return (
    <div className="space-y-3">
      <Group title="Portfolio">
        <FormField label="Stocks to rank" htmlFor="me-universe">
          <select id="me-universe" value={v.universe} onChange={(e) => set("universe", e.target.value)} className={INPUT_CLASS}>
            {(options?.universes || []).map((u) => <option key={u.id} value={u.id}>{u.label}</option>)}
          </select>
        </FormField>
        <FormField label="From" unit="date" htmlFor="me-start">
          <input id="me-start" type="date" value={v.start} onChange={(e) => set("start", e.target.value)} className={INPUT_CLASS} />
        </FormField>
        <FormField label="To" unit="date" hint="Empty = the last stored day" htmlFor="me-end">
          <input id="me-end" type="date" value={v.end || ""} onChange={(e) => set("end", e.target.value || null)}
            className={INPUT_CLASS} />
        </FormField>
        <FormField label="Hold the top" unit="stocks" htmlFor="me-topn" hint={`N, 1 to ${Math.min(50, size)}`}>
          <NumberInput id="me-topn" value={v.top_n} min={1} max={Math.min(50, size)} onChange={(n) => set("top_n", n)} />
        </FormField>
        <FormField label="Keep while in the top" unit="rank" htmlFor="me-holdrank"
          hint={`M, ${v.top_n} to ${size}; M = N means no buffer`}>
          <NumberInput id="me-holdrank" value={v.hold_rank} min={v.top_n} max={size} onChange={(n) => set("hold_rank", n)} />
        </FormField>
        <FormField label="Rebalance" htmlFor="me-rebalance">
          <select id="me-rebalance" value={v.rebalance} onChange={(e) => set("rebalance", e.target.value)} className={INPUT_CLASS}>
            <option value="monthly">Monthly</option>
            <option value="weekly">Weekly</option>
          </select>
        </FormField>
        <FormField label="Weights" htmlFor="me-weighting">
          <select id="me-weighting" value={v.weighting} onChange={(e) => set("weighting", e.target.value)} className={INPUT_CLASS}>
            <option value="equal">Equal</option>
            <option value="inverse_vol">Inverse volatility (calmer stocks get more)</option>
          </select>
        </FormField>
        {v.weighting === "inverse_vol" && (
          <FormField label="Volatility over" unit="trading days" htmlFor="me-voldays">
            <NumberInput id="me-voldays" value={v.vol_days} min={5} max={252} onChange={(n) => set("vol_days", n)} />
          </FormField>
        )}
        <FormField label="Capital" unit="Rs" htmlFor="me-capital">
          <NumberInput id="me-capital" value={v.capital} min={10000} step={10000} width="w-36" onChange={(n) => set("capital", n)} />
        </FormField>
        <FormField label="Re-size a kept stock when off by" unit="% of its weight" htmlFor="me-band">
          <NumberInput id="me-band" value={v.drift_band_pct} min={0} max={100} onChange={(n) => set("drift_band_pct", n)} />
        </FormField>
      </Group>

      <Group title="Ranking and filters">
        <FormField label="Momentum over">
          <div className="flex flex-wrap gap-3 py-2">
            {LOOKBACKS.map(([days, text]) => (
              <Check key={days} checked={f.lookbacks.includes(days)} onChange={(on) => toggleLookback(days, on)}>{text}</Check>
            ))}
          </div>
        </FormField>
        <FormField label="Score" htmlFor="me-method">
          <select id="me-method" value={f.method} onChange={(e) => setFilter("method", e.target.value)} className={INPUT_CLASS}>
            {(options?.methods || ["return"]).map((m) => <option key={m} value={m}>{METHOD_TEXT[m] || m}</option>)}
          </select>
        </FormField>
        <FormField label="Skip the latest" unit="trading days" htmlFor="me-skip" hint="21 = leave out the last month">
          <NumberInput id="me-skip" value={f.skip_recent} min={0} max={63} onChange={(n) => setFilter("skip_recent", n)} />
        </FormField>
        <FormField label="Price above its average of" unit="days, 0 = off" htmlFor="me-ma">
          <NumberInput id="me-ma" value={f.ma_days} min={0} max={400} onChange={(n) => setFilter("ma_days", n)} />
        </FormField>
        <FormField label="Traded value at least" unit="Rs crore a day, 0 = off" htmlFor="me-value">
          <NumberInput id="me-value" value={f.min_traded_value_cr} min={0} step={0.5} onChange={(n) => setFilter("min_traded_value_cr", n)} />
        </FormField>
        <FormField label="Skip a one-day move above" unit="%, 0 = off" htmlFor="me-move">
          <NumberInput id="me-move" value={f.max_one_day_move_pct} min={0} max={100} onChange={(n) => setFilter("max_one_day_move_pct", n)} />
        </FormField>
        <FormField label="Nifty 50 filter">
          <div className="space-y-2 py-2">
            <Check checked={f.index_filter} onChange={(on) => setFilter("index_filter", on)}>
              Buy only when Nifty 50 is above its average
            </Check>
          </div>
        </FormField>
        {f.index_filter && (
          <FormField label="Nifty 50 average of" unit="days" htmlFor="me-indexma">
            <NumberInput id="me-indexma" value={f.index_ma_days} min={10} max={400} onChange={(n) => setFilter("index_ma_days", n)} />
          </FormField>
        )}
        <FormField label="Money not in stocks goes to" htmlFor="me-safe">
          <select id="me-safe" value={f.safe_asset} onChange={(e) => setFilter("safe_asset", e.target.value)} className={INPUT_CLASS}>
            <option value="LIQUIDBEES">LIQUIDBEES (liquid ETF)</option>
            <option value="CASH">Cash (earns nothing)</option>
          </select>
        </FormField>
        {f.safe_asset === "LIQUIDBEES" && (
          <FormField label="LIQUIDBEES return" unit="% a year" htmlFor="me-yield">
            <NumberInput id="me-yield" value={v.liquid_yield_pct} min={0} max={15} step={0.25} onChange={(n) => set("liquid_yield_pct", n)} />
          </FormField>
        )}
      </Group>

      <Group title="Costs and tax (equity delivery)" open={false}>
        <FormField label="Slippage" unit="% of price" htmlFor="me-slip">
          <NumberInput id="me-slip" value={v.costs.slippage_value} min={0} max={2} step={0.01} onChange={(n) => setCost("slippage_value", n)} />
        </FormField>
        <FormField label="DP charge" unit="Rs per sell order" htmlFor="me-dp">
          <NumberInput id="me-dp" value={v.costs.dp_charge} min={0} step={0.5} onChange={(n) => setCost("dp_charge", n)} />
        </FormField>
        <FormField label="Charges">
          <div className="py-2">
            <Check checked={v.costs.include_taxes} onChange={(on) => setCost("include_taxes", on)}>
              STT, exchange, SEBI, stamp and GST
            </Check>
          </div>
        </FormField>
        <FormField label="Capital gains tax">
          <div className="py-2">
            <Check checked={v.tax.enabled} onChange={(on) => setTax("enabled", on)}>
              Pay it at each rebalance (short-term 20 %, long-term 12.5 % above Rs 1.25 lakh, + 4 % cess)
            </Check>
          </div>
        </FormField>
        {v.tax.enabled && (
          <FormField label="Your income tax slab" unit="%" htmlFor="me-slab" hint="Used for LIQUIDBEES gains">
            <NumberInput id="me-slab" value={v.tax.slab_pct} min={0} max={45} onChange={(n) => setTax("slab_pct", n)} />
          </FormField>
        )}
      </Group>
    </div>
  );
}
