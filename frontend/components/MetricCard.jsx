"use client";
// One number on a tile: the label, the big value (monospace), and the change with an arrow, a sign and a
// profit / loss colour (the arrow and sign mean colour is never the only signal).
//
//   <MetricCard label="Nifty 50" value={formatNumber(25120)} change={0.48} />            change in %
//   <MetricCard label="P&L" value="Rs 4,875" change={4875} changeKind="rs" sparkline={[1, 3, 2, 5]} />
//   changeText: your own words for the change (its colour still comes from `change`).
//   invert:     a rise is bad news (India VIX), so show it as a loss.     loading: grey shapes instead.
//   valueClassName: e.g. pnlClass(pnl) to colour a P&L value (give it a sign too, so colour is not the only cue).
import { ArrowDownRight, ArrowUpRight, Info, Minus } from "lucide-react";

import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { formatPct, formatRs, isEmpty } from "@/lib/format";

// A tiny line chart of recent values (no axes), coloured like the change.
export function Sparkline({ values = [], className = "", width = 96, height = 28 }) {
  const points = values.map(Number).filter((v) => Number.isFinite(v));
  if (points.length < 2) return null;
  const low = Math.min(...points);
  const high = Math.max(...points);
  const span = high - low || 1;
  const path = points.map((v, i) => `${(i / (points.length - 1)) * width},${height - ((v - low) / span) * height}`)
    .join(" ");
  return (
    <svg viewBox={`0 0 ${width} ${height}`} width={width} height={height} className={className} aria-hidden="true"
      preserveAspectRatio="none">
      <polyline points={path} fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinejoin="round"
        strokeLinecap="round" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export default function MetricCard({ label, value, change, changeKind = "pct", changeText, invert = false, help,
  sparkline, loading = false, valueClassName = "", className = "" }) {
  if (loading) {
    return (
      <Card className={`gap-2 p-4 ${className}`}>
        <Skeleton className="h-3 w-24" /><Skeleton className="h-7 w-32" /><Skeleton className="h-3 w-20" />
      </Card>
    );
  }
  const n = isEmpty(change) ? null : Number(change);
  const good = n === null || n === 0 ? null : invert ? n < 0 : n > 0;
  const tone = good === null ? "text-muted-foreground" : good ? "text-profit" : "text-loss";
  const Arrow = n > 0 ? ArrowUpRight : n < 0 ? ArrowDownRight : Minus;
  const text = n === null ? null : changeText ?? (changeKind === "rs" ? formatRs(n, 2, true) : formatPct(n, 2, true));
  return (
    <Card className={`gap-1 p-4 ${className}`}>
      <div className="flex items-center gap-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        <span className="truncate">{label}</span>
        {help && (
          <Tooltip>
            <TooltipTrigger asChild>
              <button type="button" className="rounded-sm hover:text-foreground" aria-label={`About ${label}`}>
                <Info className="size-3.5" />
              </button>
            </TooltipTrigger>
            <TooltipContent className="max-w-xs normal-case">{help}</TooltipContent>
          </Tooltip>
        )}
      </div>
      <div className="flex items-end justify-between gap-2">
        <p className={`num truncate text-2xl font-semibold leading-tight ${valueClassName}`}>{isEmpty(value) ? "–" : value}</p>
        {sparkline && <Sparkline values={sparkline} className={tone} />}
      </div>
      {text && (
        <p className={`num flex items-center gap-1 text-sm font-medium ${tone}`}>
          <Arrow className="size-4" aria-hidden="true" />
          <span>{text}</span>
          {n !== null && n !== 0 && <span className="sr-only">{good ? "(good)" : "(bad)"}</span>}
        </p>
      )}
    </Card>
  );
}
