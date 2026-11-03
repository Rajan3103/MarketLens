"use client";
// Live Algo Trading & Bot Monitor (module 11): banner, status cards, a warning when the live runner is not
// running, and four tabs kept in the address (?tab=deploy|monitor|limits|errors).
// Everything refreshes every 10 seconds in market hours. ?strategy=<id> (Strategy Library -> Deploy live)
// picks that strategy on the Deploy tab. Paper or live follows TRADING_MODE in .env.
import { useSearchParams } from "next/navigation";
import { useState } from "react";

import ErrorBox from "@/components/ErrorBox";
import HistoricalNote from "@/components/HistoricalNote";
import Loading from "@/components/Loading";
import Tabs from "@/components/Tabs";
import { apiGet, apiPost } from "@/lib/api";
import { isMarketOpen } from "@/lib/market";
import usePolling from "@/lib/usePolling";

import DeployTab from "./DeployTab";
import ErrorsTab from "./ErrorsTab";
import LimitsTab from "./LimitsTab";
import MonitorTab from "./MonitorTab";
import { Banner, StatusCards } from "./StatusPanel";

// The warning (with a start button) when the live runner is not running.
function RunnerWarning({ onStarted }) {
  const [answer, setAnswer] = useState(null);
  const [problem, setProblem] = useState(null);

  // POST /api/live/runner/start.
  const start = async () => {
    setProblem(null);
    try {
      setAnswer((await apiPost("live/runner/start")).message);
      onStarted();
    } catch (err) {
      setProblem(err);
    }
  };

  return (
    <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-amber-300 bg-amber-50 px-4 py-3 text-sm dark:border-amber-800 dark:bg-amber-950/40">
      <p>The live runner is not running, so deployed strategies do not trade. Start it here, or in a terminal:{" "}
        <code className="rounded bg-white/70 px-1 dark:bg-black/30">python -m marketlens.trading.live_runner</code>{" "}
        (add <code>--once</code> for a single cycle).</p>
      <button onClick={start} className="rounded-md bg-amber-600 px-3 py-1.5 font-semibold text-white hover:bg-amber-700">
        ▶ Start the live runner
      </button>
      {answer && <p className="w-full text-green-700 dark:text-green-400">{answer}</p>}
      {problem && <div className="w-full"><ErrorBox error={problem} /></div>}
    </div>
  );
}

export default function LiveTrading() {
  const fromLibrary = Number(useSearchParams().get("strategy")) || null;
  const [picked, setPicked] = useState(fromLibrary);
  const [marketOpen] = useState(() => isMarketOpen());
  const { data: status, error, refresh } = usePolling(() => apiGet("live/status"), 10, { enabled: marketOpen });

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold">🤖 Live Algo Trading &amp; Bot Monitor</h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Deploy strategies from the Strategy Library, watch them run, and stop everything when needed. Every order goes
          through the order service (risk checks, kill switch, Confirm in live mode).
          {marketOpen ? " Refreshes every 10 seconds." : " Market closed: no auto refresh."}
        </p>
      </div>
      {error && !status && <ErrorBox error={error} onRetry={refresh} />}
      {!status && !error && <Loading text="Checking the trading status…" />}
      {status && (
        <>
          <Banner status={status} />
          <StatusCards status={status} onChanged={refresh} />
          {status.cards.database.ok && !status.cards.runner.alive && <RunnerWarning onStarted={refresh} />}
          {status.cards.database.ok ? (
            <Tabs tabs={[
              { id: "deploy", label: "🚀 Deploy", content: (
                <DeployTab status={status} picked={picked} setPicked={setPicked} marketOpen={marketOpen} />) },
              { id: "monitor", label: "📡 Monitor", content: <MonitorTab marketOpen={marketOpen} /> },
              { id: "limits", label: "🛑 Loss limits & kill switch", content: (
                <LimitsTab status={status} marketOpen={marketOpen} onChanged={refresh} />) },
              { id: "errors", label: "🧾 Error log", content: <ErrorsTab marketOpen={marketOpen} /> },
            ]} />
          ) : (
            <ErrorBox message="The database is not connected, so nothing can be deployed or monitored. Start PostgreSQL and check DATABASE_URL in .env." />
          )}
        </>
      )}
      <HistoricalNote />
    </div>
  );
}
