"use client";
// Loss limits tab: the daily limit (MAX_DAILY_LOSS_RS) and each strategy's limit as progress bars, and the two
// emergency buttons (kill switch, square off everything), each needing a tick box and a second click.
// Data: GET /api/live/limits, every 10 s in market hours.
import { useState } from "react";

import DangerButton from "@/components/DangerButton";
import ErrorBox from "@/components/ErrorBox";
import Loading from "@/components/Loading";
import { Bar } from "@/components/ProgressBar";
import { apiGet, apiPost } from "@/lib/api";
import { formatRs } from "@/lib/format";
import usePolling from "@/lib/usePolling";

// Green below half the limit, amber up to 80 %, red above.
function tone(used) {
  return used >= 0.8 ? "red" : used >= 0.5 ? "amber" : "green";
}

export default function LimitsTab({ status, marketOpen, onChanged }) {
  const { data, error, refresh } = usePolling(() => apiGet("live/limits"), 10, { enabled: marketOpen });
  const [answer, setAnswer] = useState(null);
  const [problem, setProblem] = useState(null);

  // Call one of the emergency endpoints, then refresh everything.
  const send = async (path, body) => {
    setAnswer(null);
    setProblem(null);
    try {
      setAnswer((await apiPost(path, body)).message);
      refresh();
      onChanged?.();
    } catch (err) {
      setProblem(err);
    }
  };

  if (error && !data) return <ErrorBox error={error} onRetry={refresh} />;
  if (!data) return <Loading text="Reading today's P&L…" />;
  const d = data.daily;
  const killed = data.kill_switch.on;
  return (
    <div className="space-y-6">
      <section className="space-y-2">
        <h2 className="text-lg font-semibold">Daily loss limit (all strategies)</h2>
        <Bar value={d.used * 100} tone={tone(d.used)}
          label={`Today's P&L ${formatRs(d.pnl, 0)} · limit ${formatRs(d.limit, 0)} (MAX_DAILY_LOSS_RS)`} />
        <p className="text-xs text-slate-500">When it is reached the kill switch switches on by itself
          {data.square_off_on_kill ? " and every position is closed (SQUARE_OFF_ON_KILL=true)." : "; open positions stay (SQUARE_OFF_ON_KILL=false)."}</p>
      </section>
      <section className="space-y-3">
        <h2 className="text-lg font-semibold">Per-strategy loss limits</h2>
        {!data.strategies.length && <p className="text-sm text-slate-500">No strategy has traded today.</p>}
        {data.strategies.map((p) => (
          <Bar key={p.strategy_id} value={p.used * 100} tone={tone(p.used)}
            label={`${p.strategy}: ${formatRs(p.pnl, 0)} · limit ${p.limit ? formatRs(p.limit, 0) : "none"}`} />
        ))}
      </section>

      {answer && <p className="rounded-md bg-slate-100 px-3 py-2 text-sm dark:bg-slate-800">{answer}</p>}
      {problem && <ErrorBox error={problem} />}
      <div className="grid gap-4 md:grid-cols-2">
        <section className="space-y-3 rounded-lg border-2 border-red-300 p-4 dark:border-red-900">
          <h2 className="font-semibold">🛑 Kill switch: stop all trading</h2>
          <p className="text-sm text-slate-600 dark:text-slate-400">Blocks every new order for the rest of the day.
            Exits of open positions (SL, target, Stop) still go through.</p>
          {killed ? (
            <>
              <p className="text-sm font-semibold text-red-700 dark:text-red-400">ON: {data.kill_switch.reason}</p>
              <button onClick={() => send("live/unkill")}
                className="rounded-md border border-slate-300 px-4 py-2 text-sm hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800">
                Switch the kill switch OFF
              </button>
            </>
          ) : (
            <DangerButton tick="Yes, block all new orders today" label="Kill switch ON"
              onConfirm={() => send("live/kill", { confirm: "KILL" })} />
          )}
        </section>
        <section className="space-y-3 rounded-lg border-2 border-red-300 p-4 dark:border-red-900">
          <h2 className="font-semibold">⛔ Square off everything</h2>
          <p className="text-sm text-slate-600 dark:text-slate-400">
            {status.live ? "Sends Dhan's exit-all (every position, pending orders cancelled)"
              : "Closes every paper position at its last price"}, switches the kill switch on and stops every deployed
            strategy.
          </p>
          <DangerButton tick="Yes, close every position now" label="Square off everything"
            onConfirm={() => send("live/square-off", { confirm: "SQUARE OFF" })} />
        </section>
      </div>
    </div>
  );
}
