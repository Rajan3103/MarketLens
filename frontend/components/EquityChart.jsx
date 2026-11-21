"use client";
// Account value over time for one or more curves (strategy vs Nifty 50, all on the same starting capital) with
// a legend, and the drawdown (% below the high) as a red area underneath on the same time axis.
// lightweight-charts v5, browser only; follows the theme and resizes with its card; TradingView logo kept.
//
//   <EquityChart curves={[{ name: "Strategy", points: result.equity }, …result.benchmarks]}
//     drawdown={result.drawdown} />          points: [{ time: "2025-01-01", value: 500000 }, …]
import { useTheme } from "next-themes";
import { useEffect, useMemo, useRef, useState } from "react";

import { chartTimeLabel } from "@/components/PriceChart";
import { Skeleton } from "@/components/ui/skeleton";
import { chartTimeToDate, readChartTheme, toChartTime, withAlpha } from "@/lib/chartTheme";
import { formatPct, formatRs } from "@/lib/format";

// Short rupees for the price axis: 1,25,000 -> "1.25L", 2,50,00,000 -> "2.5Cr".
export function shortRs(value) {
  const n = Math.abs(value);
  const sign = value < 0 ? "-" : "";
  if (n >= 1e7) return `${sign}${(n / 1e7).toFixed(2).replace(/\.?0+$/, "")}Cr`;
  if (n >= 1e5) return `${sign}${(n / 1e5).toFixed(2).replace(/\.?0+$/, "")}L`;
  if (n >= 1e3) return `${sign}${(n / 1e3).toFixed(1).replace(/\.0$/, "")}k`;
  return `${sign}${n.toFixed(0)}`;
}

// Points as chart data: times converted, sorted, one point per time, empty values dropped.
function prepare(points = []) {
  const seen = new Set();
  return points.filter((p) => p.value !== null && p.value !== undefined)
    .map((p) => ({ time: toChartTime(p.time), value: Number(p.value) }))
    .filter((p) => (seen.has(p.time) ? false : seen.add(p.time)))
    .sort((a, b) => (a.time > b.time ? 1 : -1));
}

// The options that follow the theme.
function themeOptions(t) {
  return {
    layout: { background: { type: "solid", color: "transparent" }, textColor: t.mutedForeground, fontFamily: t.font,
      attributionLogo: true, panes: { separatorColor: t.border } },
    grid: { vertLines: { color: withAlpha(t.border, 0.6) }, horzLines: { color: withAlpha(t.border, 0.6) } },
    rightPriceScale: { borderColor: t.border },
    timeScale: { borderColor: t.border },
  };
}

export default function EquityChart({ curves = [], drawdown = [], height = 420 }) {
  const box = useRef(null);
  const chart = useRef(null);
  const [ready, setReady] = useState(false);
  const [hover, setHover] = useState(null);
  const { resolvedTheme } = useTheme();
  const lines = useMemo(() => curves.filter((c) => c?.points?.length).map((c) => ({ ...c, data: prepare(c.points) })),
    [curves]);
  const dd = useMemo(() => prepare(drawdown), [drawdown]);

  // Create the chart once in the browser; remove it when leaving.
  useEffect(() => {
    let gone = false;
    import("lightweight-charts").then((lib) => {
      if (gone || !box.current) return;
      const api = lib.createChart(box.current, { autoSize: true, ...themeOptions(readChartTheme()),
        localization: { timeFormatter: chartTimeLabel } });
      chart.current = { api, lib, series: [] };
      setReady(true);
    });
    return () => {
      gone = true;
      chart.current?.api.remove();
      chart.current = null;
    };
  }, []);

  // Draw the curves and the drawdown (again when the data or the theme changes).
  useEffect(() => {
    const c = chart.current;
    if (!ready || !c) return;
    const t = readChartTheme();
    const { api, lib } = c;
    api.applyOptions(themeOptions(t));
    c.series.forEach((s) => api.removeSeries(s));
    c.series = [];
    c.lines = lines.map((line, i) => {
      const color = line.color || (i === 0 ? t.primary : t.series[i % t.series.length]);
      const series = api.addSeries(lib.LineSeries, { color, lineWidth: i === 0 ? 2 : 1, priceLineVisible: false,
        lastValueVisible: i === 0, priceFormat: { type: "custom", formatter: shortRs, minMove: 1 } });
      series.setData(line.data);
      c.series.push(series);
      return { name: line.name, color, series };
    });
    if (dd.length) {
      const area = api.addSeries(lib.AreaSeries, { lineColor: t.loss, topColor: withAlpha(t.loss, 0.05),
        bottomColor: withAlpha(t.loss, 0.35), lineWidth: 1, priceLineVisible: false, invertFilledArea: true,
        priceFormat: { type: "custom", formatter: (v) => `${v.toFixed(1)}%`, minMove: 0.01 } }, 1);
      area.setData(dd);
      c.series.push(area);
      c.dd = area;
      const panes = api.panes();
      panes[0]?.setStretchFactor(3);
      panes[1]?.setStretchFactor(1);
    } else {
      c.dd = null;
    }
    api.timeScale().fitContent();
  }, [ready, lines, dd, resolvedTheme]);

  // The legend shows the values under the crosshair (or the last ones).
  useEffect(() => {
    const c = chart.current;
    if (!ready || !c) return undefined;
    const move = (param) => {
      if (!param.time || !param.point) { setHover(null); return; }
      setHover({ time: param.time, values: (c.lines || []).map((l) => param.seriesData.get(l.series)?.value),
        dd: c.dd ? param.seriesData.get(c.dd)?.value : undefined });
    };
    c.api.subscribeCrosshairMove(move);
    return () => c.api.unsubscribeCrosshairMove(move);
  }, [ready, lines, dd]);

  const last = (data) => data[data.length - 1];
  const time = hover?.time ?? last(lines[0]?.data || [])?.time;
  const values = hover ? hover.values : lines.map((l) => last(l.data)?.value);
  const ddValue = hover ? hover.dd : last(dd)?.value;
  return (
    <div className="space-y-2 rounded-xl border bg-card p-3">
      <div className="num flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        {time !== undefined && <span className="text-foreground">{chartTimeLabel(time)}</span>}
        {lines.map((l, i) => (
          <span key={l.name} className="flex items-center gap-1.5">
            <span className="inline-block h-0.5 w-4 rounded"
              style={{ background: l.color || (i === 0 ? "var(--primary)" : `var(--chart-${(i % 5) + 1})`) }} />
            {l.name} <b className="text-foreground">{formatRs(values[i], 0)}</b>
          </span>
        ))}
        {dd.length > 0 && (
          <span className="flex items-center gap-1.5">
            <span className="inline-block size-2.5 rounded-sm bg-loss/40" /> Drawdown
            <b className="text-loss">{formatPct(ddValue, 2)}</b>
          </span>
        )}
      </div>
      <div className="relative w-full" style={{ height }}>
        {!ready && <Skeleton className="absolute inset-0 rounded-lg" />}
        <div ref={box} className="absolute inset-0" />
      </div>
      {lines.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {formatDateRange(lines[0].data)} · all curves start with the same capital
        </p>
      )}
    </div>
  );
}

// "01-Jan-2025 to 31-Dec-2025" for a curve.
function formatDateRange(data) {
  if (!data.length) return "";
  const label = (t) => chartTimeLabel(typeof t === "number" ? t : chartTimeToDate(t).toISOString().slice(0, 10));
  return `${label(data[0].time)} to ${label(data[data.length - 1].time)}`;
}
