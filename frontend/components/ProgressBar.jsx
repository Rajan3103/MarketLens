"use client";
// A progress bar. Either show a number you already have, or follow a background job of the API.
//
//   <ProgressBar value={45} label="Daily loss limit used" />
//   <ProgressBar jobId={id} onDone={(job) => setResult(job.result)} />    // asks /api/jobs/{id} every second
//
// With a job: shows the % and message, a Cancel button while it runs, and the result's status at the end.
import { useEffect, useRef, useState } from "react";

import ErrorBox from "@/components/ErrorBox";
import { apiGet, apiPost } from "@/lib/api";
import usePolling from "@/lib/usePolling";

const STATUS_TEXT = { queued: "Waiting to start", running: "Working", done: "Done", failed: "Failed",
  cancelled: "Cancelled" };

// The bar itself: 0-100 %, blue while working, green when done, red when it failed.
export function Bar({ value = 0, label, tone = "blue" }) {
  const pct = Math.min(Math.max(Number(value) || 0, 0), 100);
  const colors = { blue: "bg-blue-600", green: "bg-green-600", red: "bg-red-600", amber: "bg-amber-500" };
  return (
    <div className="space-y-1">
      {label && (
        <div className="flex justify-between text-sm">
          <span>{label}</span><span className="tabular-nums text-slate-500">{pct.toFixed(0)} %</span>
        </div>
      )}
      <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-200 dark:bg-slate-800" role="progressbar"
        aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
        <div className={`h-full rounded-full transition-all ${colors[tone] || colors.blue}`} style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

// Follow one job: ask every second until it has finished.
function JobProgress({ jobId, onDone }) {
  const [finished, setFinished] = useState(false);
  const [cancelProblem, setCancelProblem] = useState("");
  const told = useRef(false);
  const { data: job, error } = usePolling(() => apiGet(`jobs/${jobId}`), 1, { key: jobId, enabled: !finished });

  useEffect(() => {
    if (!job?.finished || told.current) return;
    told.current = true;
    setFinished(true);
    onDone?.(job);
  }, [job, onDone]);

  if (error && !job) return <ErrorBox error={error} />;
  if (!job) return <Bar value={0} label="Starting…" />;
  const tone = job.status === "done" ? "green" : job.status === "failed" ? "red" : job.status === "cancelled" ? "amber" : "blue";

  // Ask the API to stop the job.
  const cancel = () => apiPost(`jobs/${jobId}/cancel`).catch((err) => setCancelProblem(err.message));

  return (
    <div className="space-y-2 rounded-lg border border-slate-200 p-4 dark:border-slate-800">
      <Bar value={job.progress} label={`${STATUS_TEXT[job.status] || job.status}${job.message ? `: ${job.message}` : ""}`} tone={tone} />
      {!job.finished && (
        <button onClick={cancel} disabled={job.cancel_requested}
          className="rounded-md border border-slate-300 px-3 py-1 text-sm hover:bg-slate-100 disabled:opacity-50 dark:border-slate-700 dark:hover:bg-slate-800">
          {job.cancel_requested ? "Stopping…" : "Cancel"}
        </button>
      )}
      {job.status === "failed" && job.error && <ErrorBox message={job.error} />}
      {cancelProblem && <ErrorBox message={cancelProblem} />}
    </div>
  );
}

export default function ProgressBar({ jobId, onDone, value, label, tone }) {
  if (jobId) return <JobProgress key={jobId} jobId={jobId} onDone={onDone} />;
  return <Bar value={value} label={label} tone={tone} />;
}
