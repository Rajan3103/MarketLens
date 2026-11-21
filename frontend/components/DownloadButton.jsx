"use client";
// Buttons that save a CSV or Excel file made by an API endpoint: a spinner while it is made, then a toast.
//
//   <DownloadButton path="market/history/download" params={{ symbol, period }} formats={["csv", "xlsx"]} />
//
// The file name comes from the API (Content-Disposition). A 401 goes to the login page.
import { Download, Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Button } from "@/components/ui/button";
import { ApiError, NOT_RUNNING, apiUrl, goToLogin } from "@/lib/api";

const LABELS = { csv: "CSV", xlsx: "Excel" };

// The file name the API suggested, or a fallback.
function fileNameOf(response, fallback) {
  const header = response.headers.get("Content-Disposition") || "";
  const match = header.match(/filename="?([^";]+)"?/i);
  return match ? match[1] : fallback;
}

// Hand a file (Blob) to the browser to save.
export function saveBlob(blob, name) {
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = name;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(link.href), 1000);
}

// Ask the API for the file and save it; returns the file name.
async function download(path, params, format) {
  const query = new URLSearchParams({ ...params, format });
  let response;
  try {
    response = await fetch(`${apiUrl(path)}?${query}`, { credentials: "include" });
  } catch {
    throw new ApiError(NOT_RUNNING);
  }
  if (response.status === 401) goToLogin(path);
  if (!response.ok) {
    const answer = await response.json().catch(() => null);
    throw new ApiError(answer?.error || NOT_RUNNING, response.status);
  }
  const name = fileNameOf(response, `marketlens.${format}`);
  saveBlob(await response.blob(), name);
  return name;
}

export default function DownloadButton({ path, params = {}, formats = ["csv"], disabled = false }) {
  const [busy, setBusy] = useState("");

  // One button press: download that format, then say how it went.
  const save = async (format) => {
    setBusy(format);
    try {
      const name = await download(path, params, format);
      toast.success("Download ready", { description: name });
    } catch (err) {
      toast.error("Download failed", { description: err.message });
    } finally {
      setBusy("");
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-2">
      {formats.map((f) => (
        <Button key={f} variant="outline" size="sm" onClick={() => save(f)} disabled={disabled || Boolean(busy)}>
          {busy === f ? <Loader2 className="animate-spin" aria-hidden="true" /> : <Download aria-hidden="true" />}
          {busy === f ? "Preparing…" : LABELS[f] || f.toUpperCase()}
        </Button>
      ))}
    </div>
  );
}
