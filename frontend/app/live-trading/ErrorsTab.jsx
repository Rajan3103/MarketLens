"use client";
// Error log tab: errors and warnings from the error_log table, filtered by period, level, module and text.
// Data: GET /api/live/errors, every 10 s in market hours.
import { useState } from "react";

import DataTable from "@/components/DataTable";
import ErrorBox from "@/components/ErrorBox";
import FormField, { INPUT_CLASS } from "@/components/FormField";
import Loading from "@/components/Loading";
import { apiGet } from "@/lib/api";
import usePolling from "@/lib/usePolling";

const COLUMNS = [
  { key: "ts", label: "Time", kind: "text" },
  { key: "level", label: "Level", kind: "text",
    render: (r) => <span className={r.level === "ERROR" ? "font-semibold text-red-600" : "text-amber-600"}>{r.level}</span> },
  { key: "module", label: "Module", kind: "text" },
  { key: "message", label: "Message", kind: "text" },
  { key: "details", label: "Details", kind: "text",
    render: (r) => <span className="break-all font-mono text-xs">{r.details}</span> },
];
const PERIODS = [[1, "Today"], [7, "Last 7 days"], [30, "Last 30 days"]];

export default function ErrorsTab({ marketOpen }) {
  const [days, setDays] = useState(1);
  const [levels, setLevels] = useState(["ERROR", "WARNING"]);
  const [module, setModule] = useState("");
  const [text, setText] = useState("");
  const params = new URLSearchParams({ days: String(days), text });
  levels.forEach((lv) => params.append("level", lv));
  if (module) params.set("module", module);
  const query = `live/errors?${params}`;
  const { data, error, refresh } = usePolling(() => apiGet(query), 10, { key: query, enabled: marketOpen });

  // Tick or untick a level (at least one stays ticked).
  const toggle = (lv, on) => {
    const next = on ? [...levels, lv] : levels.filter((x) => x !== lv);
    if (next.length) setLevels(next);
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end gap-4">
        <FormField label="Period" htmlFor="lt-days">
          <select id="lt-days" value={days} onChange={(e) => setDays(Number(e.target.value))} className={INPUT_CLASS}>
            {PERIODS.map(([d, label]) => <option key={d} value={d}>{label}</option>)}
          </select>
        </FormField>
        <FormField label="Level">
          <div className="flex gap-3 py-2 text-sm">
            {["ERROR", "WARNING"].map((lv) => (
              <label key={lv} className="flex items-center gap-1">
                <input type="checkbox" checked={levels.includes(lv)} onChange={(e) => toggle(lv, e.target.checked)} /> {lv}
              </label>
            ))}
          </div>
        </FormField>
        <FormField label="Module" htmlFor="lt-module">
          <select id="lt-module" value={module} onChange={(e) => setModule(e.target.value)} className={`${INPUT_CLASS} max-w-xs`}>
            <option value="">(all)</option>
            {(data?.modules || []).map((m) => <option key={m} value={m}>{m}</option>)}
          </select>
        </FormField>
        <FormField label="Message contains" htmlFor="lt-text">
          <input id="lt-text" value={text} onChange={(e) => setText(e.target.value)} className={`${INPUT_CLASS} w-56`} />
        </FormField>
      </div>
      {error && !data && <ErrorBox error={error} onRetry={refresh} />}
      {!data && !error && <Loading text="Reading the error log…" />}
      {data && (
        <>
          <DataTable columns={COLUMNS} rows={data.errors} pageSize={25} rowKey={(r, i) => `${r.ts}-${i}`}
            emptyText="No errors for these filters." />
          <p className="text-xs text-slate-500">The full log, with every step, is in logs/marketlens.log (and
            logs/live_runner.log).</p>
        </>
      )}
    </div>
  );
}
