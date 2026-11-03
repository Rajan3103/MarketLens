"use client";
// Momentum Engine Backtest vs Nifty (module 8): three tabs kept in the address (?tab=backtest|ranks|history).
// The settings, the running job and the last result live here, so switching tabs keeps them.
// ?strategy=ID opens a strategy from the Strategy Library (module 10).
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import ErrorBox from "@/components/ErrorBox";
import HelpNote from "@/components/HelpNote";
import HistoricalNote from "@/components/HistoricalNote";
import Loading from "@/components/Loading";
import Tabs from "@/components/Tabs";
import { apiGet } from "@/lib/api";

import BacktestTab from "./BacktestTab";
import HistoryTab from "./HistoryTab";
import RanksTab from "./RanksTab";

export default function MomentumEngine() {
  const strategyId = useSearchParams().get("strategy");
  const [options, setOptions] = useState(null);
  const [settings, setSettings] = useState(null);
  const [opened, setOpened] = useState(null);
  const [problem, setProblem] = useState(null);
  const [state, setState] = useState({ jobId: null, syncJobId: null, check: null, result: null, runId: null,
    warning: null });

  // Load the defaults, then the strategy from the library if the address names one.
  useEffect(() => {
    let stopped = false;
    (async () => {
      try {
        const answer = await apiGet("momentum/options");
        let start = answer.defaults;
        let from = null;
        if (strategyId) {
          from = await apiGet(`momentum/strategies/${encodeURIComponent(strategyId)}`);
          start = { ...answer.defaults, ...from.settings };
        }
        if (stopped) return;
        setOptions(answer);
        setSettings(start);
        setOpened(from);
      } catch (err) {
        if (!stopped) setProblem(err);
      }
    })();
    return () => { stopped = true; };
  }, [strategyId]);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold">🚀 Momentum Engine Backtest vs Nifty</h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Rank Nifty 50 or Nifty 200 stocks by past momentum, hold the top N and compare the result after costs and
          tax with Nifty 50 buy-and-hold. Prices come from your database (daily, yfinance).
        </p>
      </div>
      {problem && <ErrorBox error={problem} />}
      {!settings && !problem && <Loading text="Loading the settings…" />}
      {settings && (
        <Tabs tabs={[
          { id: "backtest", label: "Backtest", content: (
            <BacktestTab settings={settings} setSettings={setSettings} options={options} state={state}
              setState={setState} opened={opened} />) },
          { id: "ranks", label: "Current ranks", content: <RanksTab settings={settings} /> },
          { id: "history", label: "Rebalance history", content: <HistoryTab runId={state.runId} /> },
        ]} />
      )}
      <HelpNote title="How does the momentum engine work?">
        <p>At the close of the last trading day of each month (or week) every stock gets a <strong>momentum
          score</strong>: its average return over the chosen periods (3, 6 and 12 months by default), leaving out the
          latest month. Stocks must also be above their 200-day average and, optionally, trade enough each day.</p>
        <p>The portfolio holds the <strong>top N</strong>. A stock already held is kept while it stays in the
          <strong> top M</strong>, which cuts trading and costs. Trades happen at the close of the <em>next</em> trading
          day, so the backtest never uses a price it could not have known.</p>
        <p>With the Nifty 50 filter on, nothing is bought while Nifty 50 is below its 200-day average; the money waits
          in LIQUIDBEES (or cash). Costs are equity-delivery charges plus slippage; capital gains tax is paid at each
          rebalance.</p>
        <p>The stock lists are today&apos;s index members, so stocks that dropped out of the index in the past are
          missing (survivorship bias). Real results would usually have been a little lower.</p>
      </HelpNote>
      <HistoricalNote />
    </div>
  );
}
