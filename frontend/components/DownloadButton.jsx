"use client";
// Buttons that save a CSV or Excel file made by an API endpoint.
//
//   <DownloadButton path="market/history/download" params={{ symbol, period }} formats={["csv", "xlsx"]} />
//
// The file name comes from the API (Content-Disposition); a problem is shown as a readable message.
import { useState } from "react";

import { ApiError, NOT_RUNNING, apiUrl } from "@/lib/api";

const LABELS = { csv: "⬇️ CSV", xlsx: "⬇️ Excel" };

// The file name the API suggested, or a fallback.
function fileNameOf(response, fallback) {
  const header = response.headers.get("Content-Disposition") || "";
  const match = header.match(/filename="?([^";]+)"?/i);
  return match ? match[1] : fallback;
}

// Ask the API for the file and hand it to the browser to save.
async function download(path, params, format) {
  const query = new URLSearchParams({ ...params, format });
  let response;
  try {
    response = await fetch(`${apiUrl(path)}?${query}`, { credentials: "include" });
  } catch {
    throw new ApiError(NOT_RUNNING);
  }
  if (!response.ok) {
    const answer = await response.json().catch(() => null);
    throw new ApiError(answer?.error || NOT_RUNNING, response.status);
  }
  const blob = await response.blob();
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = fileNameOf(response, `marketlens.${format}`);
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

export default function DownloadButton({ path, params = {}, formats = ["csv"], disabled = false }) {
  const [busy, setBusy] = useState("");
  const [problem, setProblem] = useState("");

  // One button press: download that format.
  const save = async (format) => {
    setBusy(format);
    setProblem("");
    try {
      await download(path, params, format);
    } catch (err) {
      setProblem(err.message);
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      {formats.map((f) => (
        <button key={f} onClick={() => save(f)} disabled={disabled || Boolean(busy)}
          className="rounded-md border border-slate-300 px-3 py-1.5 text-sm font-medium hover:bg-slate-100 disabled:opacity-50
            dark:border-slate-700 dark:hover:bg-slate-800">
          {busy === f ? "Preparing…" : LABELS[f] || `⬇️ ${f.toUpperCase()}`}
        </button>
      ))}
      {problem && <span role="alert" className="text-sm text-red-600">{problem}</span>}
    </div>
  );
}
