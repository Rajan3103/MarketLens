"use client";
// Live Market Overview (module 1), the web version of dashboards/market_overview.py:
//   1. market status, index cards, Nifty 50 top gainers / losers and breadth   (LiveSection)
//   2. sector heatmap over 1 day / 1 week / 1 month                             (SectorHeatmap)
//   3. price chart with averages, crossovers, MA200, Bollinger, RSI and volume  (PriceChart)
// Sections 1 and 2 refresh every 60 seconds in market hours while the page is visible.
import { useState } from "react";

import HelpNote from "@/components/HelpNote";
import HistoricalNote from "@/components/HistoricalNote";
import { isMarketOpen } from "@/lib/market";

import LiveSection from "./LiveSection";
import PriceChart from "./PriceChart";
import SectorHeatmap from "./SectorHeatmap";

export default function MarketOverview() {
  const [marketOpen] = useState(() => isMarketOpen());

  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-2xl font-bold">📊 Live Market Overview</h1>
        <p className="text-sm text-slate-600 dark:text-slate-400">
          Indices, Nifty 50 movers and breadth, sectors, and a price chart with indicators. Uses Dhan quotes when
          the keys are set, otherwise yfinance (about 15 minutes delayed).
        </p>
      </div>

      <LiveSection marketOpen={marketOpen} />
      <hr className="border-slate-200 dark:border-slate-800" />
      <SectorHeatmap marketOpen={marketOpen} />
      <hr className="border-slate-200 dark:border-slate-800" />
      <PriceChart marketOpen={marketOpen} />

      <HelpNote title="What do these indicators mean?">
        <p>A <strong>moving average</strong> is the average closing price of the last N trading days. The fast one
          (20 days) follows the price closely; the slow one (50 days) moves more slowly.</p>
        <p>When the fast average crosses <strong>above</strong> the slow one (green ▲), recent prices are rising faster
          than the longer trend: a bullish signal. Crossing <strong>below</strong> (red ▼) is bearish.</p>
        <p><strong>RSI (14)</strong> measures how strong recent rises were against recent falls, from 0 to 100; above
          70 is often called overbought, below 30 oversold. <strong>Bollinger Bands</strong> sit 2 standard deviations
          above and below the 20-day average, so they widen when prices swing a lot.</p>
        <p>Crossovers come late and give false signals in sideways markets, so treat them as a way to study trends,
          not as instructions.</p>
      </HelpNote>
      <HistoricalNote />
    </div>
  );
}
