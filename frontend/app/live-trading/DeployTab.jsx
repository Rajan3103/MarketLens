"use client";
// Deploy tab: pick a strategy from the library, see what it still needs for live trading (a saved backtest
// and 10 forward-test days), choose lots multiplier, max loss and auto / confirm, deploy; then Start /
// Pause / Stop each deployed strategy. ?strategy=<id> (from the Strategy Library) picks it at once.
import { useState } from "react";

import DataTable from "@/components/DataTable";
import ErrorBox from "@/components/ErrorBox";
import FormField, { INPUT_CLASS } from "@/components/FormField";
import Loading from "@/components/Loading";
import { apiGet, apiPost } from "@/lib/api";
import { formatRs } from "@/lib/format";
import usePolling from "@/lib/usePolling";

const STATUS_ICONS = { running: "🟢", paused: "⏸️", stopping: "🟠", stopped: "⏹️" };
const SMALL = "rounded-md border px-2.5 py-1 text-xs font-medium disabled:opacity-40";

// ✅ or ❌ with a line of text.
function Tick({ ok, children }) {
  return <p className="text-sm">{ok ? "✅" : "❌"} {children}</p>;
}

// The form for one strategy.
function DeployForm({ strategy, status, onDeployed }) {
  const check = strategy.check;
  const [form, setForm] = useState({ lots: 1, maxLoss: 0, confirm: true, capital: 500000 });
  const [answer, setAnswer] = useState(null);
  const [problem, setProblem] = useState(null);
  const live = status.live;

  // Send POST /api/live/deploy.
  const deploy = async (event) => {
    event.preventDefault();
    setAnswer(null);
    setProblem(null);
    try {
      const body = { strategy_id: strategy.id, lots_multiplier: form.lots, max_loss_rs: form.maxLoss,
        confirm: form.confirm, capital: live ? null : form.capital };
      setAnswer((await apiPost("live/deploy", body)).message);
      onDeployed();
    } catch (err) {
      setProblem(err);
    }
  };

  return (
    <form onSubmit={deploy} className="space-y-4 rounded-lg border border-slate-200 p-4 dark:border-slate-800">
      <div className="space-y-1">
        <Tick ok={check.has_backtest}>Saved backtest</Tick>
        <Tick ok={check.forward_days >= check.min_forward_days}>
          Forward-test days: <strong>{check.forward_days} / {check.min_forward_days}</strong>
        </Tick>
        {check.missing.length > 0 && (
          <p className={`rounded-md px-3 py-2 text-sm ${live ? "bg-red-50 text-red-800 dark:bg-red-950 dark:text-red-200"
            : "bg-blue-50 text-blue-900 dark:bg-blue-950 dark:text-blue-200"}`}>
            {live ? "Live deployment blocked. Missing: " : "Paper deployment is allowed now; before LIVE it still needs: "}
            {check.missing.join("; ")}.
          </p>
        )}
      </div>
      <div className="flex flex-wrap items-end gap-4">
        <FormField label="Lots multiplier" unit="× every leg" htmlFor="lt-lots">
          <input id="lt-lots" type="number" min={1} max={50} value={form.lots} className={`${INPUT_CLASS} w-24`}
            onChange={(e) => setForm({ ...form, lots: Number(e.target.value) || 1 })} />
        </FormField>
        <FormField label="Max loss for this strategy today" unit="Rs, 0 = none" htmlFor="lt-maxloss">
          <input id="lt-maxloss" type="number" min={0} step={500} value={form.maxLoss} className={`${INPUT_CLASS} w-32`}
            onChange={(e) => setForm({ ...form, maxLoss: Number(e.target.value) || 0 })} />
        </FormField>
        <FormField label="Orders" htmlFor="lt-confirm">
          <select id="lt-confirm" value={form.confirm ? "confirm" : "auto"} className={INPUT_CLASS}
            onChange={(e) => setForm({ ...form, confirm: e.target.value === "confirm" })}>
            <option value="confirm">Confirm each (Telegram or Monitor tab)</option>
            <option value="auto">Automatic</option>
          </select>
        </FormField>
        {!live && (
          <FormField label="Paper capital" unit="Rs" htmlFor="lt-capital">
            <input id="lt-capital" type="number" min={10000} step={50000} value={form.capital} className={`${INPUT_CLASS} w-36`}
              onChange={(e) => setForm({ ...form, capital: Number(e.target.value) || 10000 })} />
          </FormField>
        )}
        <button type="submit" disabled={!check.can_deploy}
          className={`rounded-md px-4 py-2 text-sm font-semibold text-white disabled:opacity-40 ${live ? "bg-red-600 hover:bg-red-700" : "bg-blue-600 hover:bg-blue-700"}`}>
          🚀 Deploy in {status.mode} mode
        </button>
      </div>
      {live && !form.confirm && !status.auto_trade && (
        <p className="text-xs text-slate-500">AUTO_TRADE=false in .env, so orders still wait for your Confirm.</p>
      )}
      {live && form.confirm && (
        <p className="text-xs text-slate-500">In confirm mode stop-loss and target exits also wait for your Confirm
          (cancelled after {status.confirm_timeout_min} minutes).</p>
      )}
      {answer && <p className="text-sm text-green-700 dark:text-green-400">{answer}</p>}
      {problem && <ErrorBox error={problem} />}
    </form>
  );
}

export default function DeployTab({ status, picked, setPicked, marketOpen }) {
  const lib = usePolling(() => apiGet("live/strategies"), 0);
  const deps = usePolling(() => apiGet("live/deployments"), 10, { enabled: marketOpen });
  const [problem, setProblem] = useState(null);
  const strategies = lib.data?.strategies || [];
  const chosenId = picked ?? strategies[0]?.id ?? null;
  const strategy = strategies.find((s) => s.id === chosenId);

  // Start (running), pause or stop one deployment.
  const setStatus = async (sid, next) => {
    setProblem(null);
    try {
      await apiPost(`live/deployments/${sid}/status`, { status: next });
      deps.refresh();
    } catch (err) {
      setProblem(err);
    }
  };

  const columns = [
    { key: "name", label: "Strategy", kind: "text",
      render: (r) => <span>{STATUS_ICONS[r.status] || ""} <strong>{r.name}</strong></span> },
    { key: "mode", label: "Mode", kind: "text", render: (r) => r.mode.toUpperCase() },
    { key: "status", label: "Status", kind: "text" },
    { key: "state", label: "State", kind: "text" },
    { key: "lots_multiplier", label: "Lots ×", kind: "int" },
    { key: "max_loss_rs", label: "Max loss", kind: "text", render: (r) => (r.max_loss_rs ? formatRs(r.max_loss_rs, 0) : "none") },
    { key: "confirm", label: "Orders", kind: "text", render: (r) => (r.confirm ? "confirm" : "auto") },
    { key: "pnl", label: "Today's P&L", kind: "rs", decimals: 0, colorSign: true },
    { key: "actions", label: "", kind: "text", render: (r) => (
      <div className="flex gap-1">
        <button className={`${SMALL} border-green-400 text-green-700`} disabled={r.status === "running"}
          onClick={() => setStatus(r.strategy_id, "running")}>▶ Start</button>
        <button className={`${SMALL} border-slate-300`} disabled={r.status !== "running"}
          onClick={() => setStatus(r.strategy_id, "paused")}>⏸ Pause</button>
        <button className={`${SMALL} border-red-400 text-red-700`} disabled={["stopping", "stopped"].includes(r.status)}
          onClick={() => setStatus(r.strategy_id, "stopping")}>⏹ Stop</button>
      </div>) },
    { key: "message", label: "Message", kind: "text" },
  ];

  return (
    <div className="space-y-6">
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Deploy a strategy from the library</h2>
        {lib.error && !lib.data && <ErrorBox error={lib.error} onRetry={lib.refresh} />}
        {!lib.data && !lib.error && <Loading text="Reading the Strategy Library…" />}
        {lib.data && !strategies.length && (
          <p className="text-sm text-slate-500">No runnable strategies yet. Save one from the Options Backtester (4) or
            make an indicator strategy on the Forward Test page (6), forward-test it, then deploy it here.</p>
        )}
        {strategies.length > 0 && (
          <FormField label="Strategy" htmlFor="lt-strategy">
            <select id="lt-strategy" value={chosenId ?? ""} onChange={(e) => setPicked(Number(e.target.value))}
              className={`${INPUT_CLASS} max-w-xl`}>
              {strategies.map((s) => <option key={s.id} value={s.id}>{s.name} ({s.kind}, {s.underlying})</option>)}
            </select>
          </FormField>
        )}
        {picked && lib.data && !strategy && (
          <ErrorBox message={`Strategy ${picked} from the address cannot be run here (only options and indicator strategies can).`} />
        )}
        {strategy && <DeployForm key={strategy.id} strategy={strategy} status={status}
          onDeployed={() => { deps.refresh(); lib.refresh(); }} />}
      </section>

      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Deployed strategies</h2>
        {problem && <ErrorBox error={problem} />}
        {deps.error && !deps.data && <ErrorBox error={deps.error} onRetry={deps.refresh} />}
        {deps.data && (
          <DataTable columns={columns} rows={deps.data.deployments} rowKey={(r) => r.strategy_id} emptyText="None yet." />
        )}
        <p className="text-xs text-slate-500">Pause: no new orders, open positions stay. Stop: this strategy&apos;s positions
          are closed at the runner&apos;s next minute, then it stops.</p>
      </section>
    </div>
  );
}
