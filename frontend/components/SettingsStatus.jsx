"use client";
// Settings & Status: which keys are set, PAPER or LIVE, and the database (from GET /api/settings/status).
import { useCallback, useEffect, useState } from "react";

import { apiGet } from "@/lib/api";

// A green tick or a red cross.
function Mark({ ok }) {
  return ok
    ? <span className="text-green-600" aria-label="set">✅</span>
    : <span className="text-red-600" aria-label="not set">❌</span>;
}

export default function SettingsStatus() {
  const [status, setStatus] = useState(null);
  const [problem, setProblem] = useState("");

  // Ask the API; the answer (or the problem) is stored when it arrives.
  const load = useCallback(() => apiGet("settings/status")
    .then((answer) => { setStatus(answer); setProblem(""); })
    .catch((err) => { setStatus(null); setProblem(err.message); }), []);
  useEffect(() => { load(); }, [load]);

  if (problem) {
    return (
      <div className="rounded-lg border border-red-300 bg-red-50 p-4 text-red-900 dark:border-red-800 dark:bg-red-950/40 dark:text-red-200">
        <p>{problem}</p>
        <button onClick={() => { setProblem(""); load(); }} className="mt-3 rounded-md bg-red-600 px-3 py-1.5 text-sm font-semibold text-white">
          Check again
        </button>
      </div>
    );
  }
  if (!status) return <p className="text-slate-500">Loading…</p>;

  const live = status.trading_mode === "LIVE";
  const dbOk = status.database.status === "ok";
  return (
    <div className="space-y-6">
      <div className="grid gap-4 sm:grid-cols-3">
        <div className={`rounded-lg p-4 text-white ${live ? "bg-red-600" : "bg-blue-600"}`}>
          <p className="text-xs uppercase opacity-80">Trading mode</p>
          <p className="text-xl font-bold">{live ? "LIVE: real money" : "PAPER: virtual money"}</p>
          <p className="text-sm opacity-90">AUTO_TRADE {status.auto_trade ? "on" : "off"}</p>
        </div>
        <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
          <p className="text-xs uppercase text-slate-500">Database</p>
          <p className="text-xl font-bold"><Mark ok={dbOk} /> {dbOk ? "Connected" : "Not connected"}</p>
          <p className="text-sm text-slate-600 dark:text-slate-400">{status.database.message}</p>
        </div>
        <div className="rounded-lg border border-slate-200 p-4 dark:border-slate-800">
          <p className="text-xs uppercase text-slate-500">Prices</p>
          <p className="text-xl font-bold">{status.demo ? "DEMO data" : "Real data (Dhan)"}</p>
          <p className="text-sm text-slate-600 dark:text-slate-400">
            {status.demo ? "Add the Dhan keys to .env for real prices." : "Dhan keys are set."}
          </p>
        </div>
      </div>

      <div className="overflow-x-auto rounded-lg border border-slate-200 dark:border-slate-800">
        <table className="w-full text-left text-sm">
          <thead className="bg-slate-50 text-slate-600 dark:bg-slate-900 dark:text-slate-400">
            <tr><th className="px-4 py-2">Key</th><th className="px-4 py-2">Set?</th><th className="px-4 py-2">What it unlocks</th></tr>
          </thead>
          <tbody>
            {status.keys.map((k) => (
              <tr key={k.name} className="border-t border-slate-200 dark:border-slate-800">
                <td className="px-4 py-2 font-mono text-xs">{k.name}</td>
                <td className="px-4 py-2"><Mark ok={k.set} /></td>
                <td className="px-4 py-2">{k.unlocks}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <p className="rounded-lg bg-slate-50 p-4 text-sm text-slate-700 dark:bg-slate-900 dark:text-slate-300">
        🔒 Keys live only in the <code>.env</code> file on this computer. It is never uploaded (it is in
        .gitignore), and this page only shows <em>whether</em> a key is set, never the key itself. Change a key in
        <code> .env</code>, then restart <code>python -m marketlens.api</code>.
      </p>
    </div>
  );
}
