"use client";
// A progress bar. Either show a number you already have, or follow a background job of the API.
//
//   <ProgressBar value={45} label="Daily loss limit used" />
//   <ProgressBar jobId={id} onDone={(job) => setResult(job.result)} />    // asks /api/jobs/{id} every second
//
// With a job: the % and message, a Cancel button while it runs, and how it ended.
import { X } from "lucide-react";
import { useState } from "react";

import ErrorBox from "@/components/ErrorBox";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { useJob } from "@/lib/queries";

const STATUS_TEXT = { queued: "Waiting to start", running: "Working", done: "Done", failed: "Failed",
  cancelled: "Cancelled" };
// The bar's colour for each tone (the text label always says the same thing in words).
const TONES = {
  blue: "[&_[data-slot=progress-indicator]]:bg-primary",
  green: "[&_[data-slot=progress-indicator]]:bg-profit",
  red: "[&_[data-slot=progress-indicator]]:bg-loss",
  amber: "[&_[data-slot=progress-indicator]]:bg-warning",
};

// The bar itself: 0-100 %, blue while working, green when done, red when it failed, amber for a warning.
export function Bar({ value = 0, label, tone = "blue" }) {
  const pct = Math.min(Math.max(Number(value) || 0, 0), 100);
  return (
    <div className="space-y-1.5">
      {label && (
        <div className="flex justify-between gap-3 text-sm">
          <span className="min-w-0 truncate">{label}</span>
          <span className="num text-muted-foreground">{pct.toFixed(0)} %</span>
        </div>
      )}
      <Progress value={pct} aria-label={label || "Progress"} className={`h-2 ${TONES[tone] || TONES.blue}`} />
    </div>
  );
}

// Follow one job until it has finished.
function JobProgress({ jobId, onDone }) {
  const { job, error, cancel } = useJob(jobId, { onDone });
  const [cancelProblem, setCancelProblem] = useState("");

  if (error && !job) return <ErrorBox error={error} />;
  if (!job) return <Bar value={0} label="Starting…" />;
  const tone = { done: "green", failed: "red", cancelled: "amber" }[job.status] || "blue";
  const label = `${STATUS_TEXT[job.status] || job.status}${job.message ? `: ${job.message}` : ""}`;
  return (
    <div className="space-y-3 rounded-xl border bg-card p-4">
      <Bar value={job.progress} label={label} tone={tone} />
      {!job.finished && (
        <Button variant="outline" size="sm" disabled={job.cancel_requested}
          onClick={() => cancel().catch((err) => setCancelProblem(err.message))}>
          <X aria-hidden="true" /> {job.cancel_requested ? "Stopping…" : "Cancel"}
        </Button>
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
