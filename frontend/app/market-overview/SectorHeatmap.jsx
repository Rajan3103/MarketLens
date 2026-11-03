"use client";
// Sector heatmap: % change of the Nifty sector indices over 1 day, 1 week or 1 month.
// Data: GET /api/market/sectors?window=... (yfinance). Refreshes every 60 seconds in market hours.
import { useState } from "react";

import ErrorBox from "@/components/ErrorBox";
import Loading from "@/components/Loading";
import PlotlyChart from "@/components/PlotlyChart";
import { apiGet } from "@/lib/api";
import usePolling from "@/lib/usePolling";

const WINDOWS = ["1 day", "1 week", "1 month"];

export default function SectorHeatmap({ marketOpen }) {
  const [windowName, setWindowName] = useState(WINDOWS[0]);
  const query = `market/sectors?window=${encodeURIComponent(windowName)}`;
  const { data, error, refresh } = usePolling(() => apiGet(query), 60, { key: query, enabled: marketOpen });
  const shown = data && data.window === windowName ? data : null;

  return (
    <section className="space-y-3">
      <h2 className="text-lg font-semibold">Sector heatmap</h2>
      <div role="radiogroup" aria-label="Change over" className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-slate-600 dark:text-slate-400">Change over</span>
        {WINDOWS.map((w) => (
          <label key={w} className={`cursor-pointer rounded-full border px-3 py-1 ${w === windowName
            ? "border-blue-500 bg-blue-50 text-blue-700 dark:bg-blue-950 dark:text-blue-300"
            : "border-slate-300 hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"}`}>
            <input type="radio" name="sector-window" value={w} checked={w === windowName}
              onChange={() => setWindowName(w)} className="sr-only" />
            {w}
          </label>
        ))}
      </div>

      {error && !shown && <ErrorBox error={error} onRetry={refresh} />}
      {!shown && !error && <Loading text="Fetching sector indices…" />}
      {shown && (shown.chart
        ? <PlotlyChart figure={shown.chart} height={360} />
        : <p className="text-sm text-slate-500">No sector data available right now.</p>)}
      {shown && <p className="text-xs text-slate-500">{shown.note}</p>}
    </section>
  );
}
