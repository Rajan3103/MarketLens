// The fixed reminder every backtest, simulator, scanner and signal result shows: a small muted bar.
import { Info } from "lucide-react";

export const HISTORICAL_NOTE = "Historical analysis for learning, not a live trading recommendation.";

// <HistoricalNote /> under any result; className adds spacing when needed.
export default function HistoricalNote({ className = "" }) {
  return (
    <p role="note" className={`mt-6 flex items-center gap-2 rounded-md bg-muted px-3 py-2 text-xs
      text-muted-foreground ${className}`}>
      <Info className="size-3.5 shrink-0" aria-hidden="true" />
      {HISTORICAL_NOTE}
    </p>
  );
}
