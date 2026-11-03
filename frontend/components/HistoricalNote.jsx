// The fixed reminder every backtest, simulator and signal page shows.
export const HISTORICAL_NOTE = "Historical analysis for learning, not a live trading recommendation.";

export default function HistoricalNote() {
  return <p className="mt-6 border-t border-slate-200 pt-3 text-xs text-slate-500 dark:border-slate-800">📘 {HISTORICAL_NOTE}</p>;
}
