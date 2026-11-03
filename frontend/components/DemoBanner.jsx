"use client";
// A yellow "DEMO DATA" banner when the API says it shows made-up prices (no Dhan keys).
// Pass demo={true/false} when the page already knows; otherwise it asks /api/health once.
import { useEffect, useState } from "react";

import { apiGet } from "@/lib/api";

export default function DemoBanner({ demo }) {
  const [fromHealth, setFromHealth] = useState(null);
  useEffect(() => {
    if (demo !== undefined) return undefined;
    let stopped = false;
    apiGet("health").then((h) => { if (!stopped) setFromHealth(Boolean(h.demo)); }).catch(() => {});
    return () => { stopped = true; };
  }, [demo]);
  const show = demo !== undefined ? demo : fromHealth;
  if (!show) return null;
  return (
    <div role="note" className="rounded-lg border border-yellow-400 bg-yellow-50 px-4 py-3 text-sm text-yellow-900
      dark:border-yellow-700 dark:bg-yellow-950/40 dark:text-yellow-200">
      <strong>DEMO DATA.</strong> The Dhan keys are not set, so these prices are made up for practice. Add
      DHAN_CLIENT_ID and DHAN_ACCESS_TOKEN to .env for real prices.
    </div>
  );
}
