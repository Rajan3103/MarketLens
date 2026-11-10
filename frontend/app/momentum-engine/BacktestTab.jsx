"use client";
// Backtest tab: the settings form, a price check (missing prices can be downloaded first), the progress bar
// of the background job, then the results against Nifty 50. Save the settings to the Strategy Library.
// Everything is kept by the parent (state / setState), so switching tabs does not lose a running backtest.
import { useCallback, useState } from "react";

import BacktestResults from "@/components/BacktestResults";
import DataTable from "@/components/DataTable";
import ErrorBox from "@/components/ErrorBox";
import FormField, { INPUT_CLASS } from "@/components/FormField";
import ProgressBar from "@/components/ProgressBar";
import { apiPost } from "@/lib/api";

import SettingsForm from "./SettingsForm";

const PROBLEM_COLUMNS = [
  { key: "symbol", label: "Symbol", kind: "text" },
  { key: "problem", label: "What is missing", kind: "text" },
  { key: "stored_from", label: "Stored from", kind: "date" },
  { key: "stored_to", label: "Stored to", kind: "date" },
];
const BUTTON = "rounded-md px-4 py-2 text-sm font-semibold disabled:opacity-50";
const PRIMARY = `${BUTTON} bg-blue-600 text-white hover:bg-blue-700`;
const SECONDARY = `${BUTTON} border border-slate-300 hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800`;

// Save the settings under a name (same name = update).
function SaveStrategy({ settings, defaultName }) {
  const [name, setName] = useState(defaultName || "");
  const [notes, setNotes] = useState("");
  const [answer, setAnswer] = useState(null);
  const [problem, setProblem] = useState(null);

  // Send the settings to POST /api/momentum/strategies.
  const save = async (event) => {
    event.preventDefault();
    setAnswer(null);
    setProblem(null);
    try {
      setAnswer(await apiPost("momentum/strategies", { name, notes, settings }));
    } catch (err) {
      setProblem(err);
    }
  };

  return (
    <form onSubmit={save} className="flex flex-wrap items-end gap-3 rounded-lg border border-slate-200 p-4 dark:border-slate-800">
      <FormField label="Save these settings as" htmlFor="me-name">
        <input id="me-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={100}
          placeholder="e.g. Nifty 200 top 20 monthly" className={`${INPUT_CLASS} w-72`} />
      </FormField>
      <FormField label="Notes" htmlFor="me-notes">
        <input id="me-notes" value={notes} onChange={(e) => setNotes(e.target.value)} className={`${INPUT_CLASS} w-72`} />
      </FormField>
      <button type="submit" className={SECONDARY}>💾 Save to Strategy Library</button>
      {answer && <p className="w-full text-sm text-green-700 dark:text-green-400">{answer.message}</p>}
      {problem && <div className="w-full"><ErrorBox error={problem} /></div>}
    </form>
  );
}

export default function BacktestTab({ settings, setSettings, options, state, setState, opened }) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState(null);
  const { jobId, syncJobId, check, result, warning } = state;

  // Start the backtest job (allowMissing: run without the symbols that have no prices).
  const start = async (allowMissing) => {
    const answer = await apiPost("momentum/backtest", { settings, allow_missing: allowMissing,
      strategy_id: opened?.strategy_id ?? null });
    setState((s) => ({ ...s, jobId: answer.job_id, check: null, result: null }));
  };

  // Check the stored prices first; run at once when nothing is missing.
  const run = async () => {
    setBusy(true);
    setProblem(null);
    try {
      const answer = await apiPost("momentum/data-check", { settings });
      setState((s) => ({ ...s, warning: answer.warning }));
      if (answer.ok) await start(false);
      else setState((s) => ({ ...s, check: answer }));
    } catch (err) {
      setProblem(err);
    } finally {
      setBusy(false);
    }
  };

  // Download what is missing (sync_prices) in the background.
  const download = async () => {
    setProblem(null);
    try {
      const answer = await apiPost("momentum/sync-prices", { settings });
      setState((s) => ({ ...s, syncJobId: answer.job_id }));
    } catch (err) {
      setProblem(err);
    }
  };

  // The backtest job finished: keep the results (and the run id for Rebalance history).
  const backtestDone = useCallback((job) => {
    if (job.status === "done") setState((s) => ({ ...s, result: job.result, runId: job.result.run_id ?? s.runId }));
  }, [setState]);

  // The download finished: check the prices again.
  const syncDone = useCallback(async (job) => {
    if (job.status !== "done") return;
    try {
      const answer = await apiPost("momentum/data-check", { settings });
      setState((s) => ({ ...s, syncJobId: null, check: answer.ok ? null : answer, warning: answer.warning }));
    } catch (err) {
      setProblem(err);
    }
  }, [settings, setState]);

  const running = Boolean(jobId) && !result;
  return (
    <div className="space-y-5">
      {opened && (
        <p className="rounded-lg border border-blue-200 bg-blue-50 px-4 py-2 text-sm text-blue-900 dark:border-blue-900 dark:bg-blue-950 dark:text-blue-200">
          Opened <strong>{opened.name}</strong> from the Strategy Library (id {opened.strategy_id}).
        </p>
      )}
      <SettingsForm value={settings} onChange={setSettings} options={options} />
      <div className="flex flex-wrap gap-3">
        <button onClick={run} disabled={busy} className={PRIMARY}>
          {busy ? "Checking prices…" : "▶ Run backtest"}
        </button>
        {(jobId || result) && (
          <button onClick={() => setState((s) => ({ ...s, jobId: null, result: null }))} className={SECONDARY}>
            Clear results
          </button>
        )}
      </div>
      {problem && <ErrorBox error={problem} />}

      {check && !check.ok && (
        <div className="space-y-3 rounded-lg border border-amber-300 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/40">
          <p className="text-sm">{check.message}</p>
          <div className="flex flex-wrap gap-3">
            <button onClick={download} disabled={Boolean(syncJobId)} className={PRIMARY}>⬇️ Download missing prices</button>
            <button onClick={() => start(true).catch(setProblem)} className={SECONDARY}>Run anyway without them</button>
          </div>
          {syncJobId && <ProgressBar jobId={syncJobId} onDone={syncDone} />}
          <DataTable columns={PROBLEM_COLUMNS} rows={check.problems} pageSize={10} rowKey={(r) => r.symbol} />
        </div>
      )}

      {warning && (
        <div role="note" className="space-y-2 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm text-amber-900
          dark:border-amber-800 dark:bg-amber-950/40 dark:text-amber-200">
          <p>⚠️ {warning}</p>
          {!(check && !check.ok) && (
            <>
              <button onClick={download} disabled={Boolean(syncJobId)} className={SECONDARY}>
                ⬇️ Download missing prices (older history too)
              </button>
              {syncJobId && <ProgressBar jobId={syncJobId} onDone={syncDone} />}
            </>
          )}
        </div>
      )}
      {jobId && <ProgressBar key={jobId} jobId={jobId} onDone={backtestDone} />}
      {running && <p className="text-xs text-slate-500">You can switch tabs; the backtest keeps running on the server.</p>}
      {result && <BacktestResults result={result} />}
      <SaveStrategy settings={settings} defaultName={opened?.name} />
    </div>
  );
}
