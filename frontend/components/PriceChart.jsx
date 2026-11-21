"use client";
// A TradingView-style price chart (lightweight-charts v5, loaded only in the browser): candles or a line, a
// volume histogram, any number of line overlays (MA 20, MA 50 …), buy / sell markers with text, a crosshair
// read-out (date, open, high, low, close, volume), range buttons, the app's theme, and it resizes with its card.
// The TradingView logo in the corner is kept on purpose: the library's licence asks for that attribution.
//
//   <PriceChart candles={[{ time: "2026-11-16", open, high, low, close, volume }, …]}
//     overlays={[{ name: "MA 20", data: [{ time, value }, …] }]}
//     markers={[{ time: "2026-11-10", side: "BUY", text: "MA cross" }]} />
//   <PriceChart mode="line" candles={…} />          (a line of the closes)
//
// time: "YYYY-MM-DD" for daily candles, or a moment (ISO text / seconds) for intraday ones (shown in IST).
import { useTheme } from "next-themes";
import { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { chartTimeToDate, readChartTheme, toChartTime, withAlpha } from "@/lib/chartTheme";
import { formatInt, formatNumber } from "@/lib/format";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAILY_RANGES = [["1M", 31], ["3M", 92], ["6M", 183], ["1Y", 366], ["All", null]];
const INTRADAY_RANGES = [["1D", 1], ["5D", 5], ["1M", 31], ["All", null]];

// "16-Nov-2026", plus " 09:15" for intraday times.
export function chartTimeLabel(time) {
  const d = chartTimeToDate(time);
  const day = `${String(d.getUTCDate()).padStart(2, "0")}-${MONTHS[d.getUTCMonth()]}-${d.getUTCFullYear()}`;
  if (typeof time !== "number") return day;
  return `${day} ${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
}

// The options that follow the theme (colours, font, grid).
function themeOptions(t) {
  return {
    layout: { background: { type: "solid", color: "transparent" }, textColor: t.mutedForeground, fontFamily: t.font,
      attributionLogo: true },
    grid: { vertLines: { color: withAlpha(t.border, 0.6) }, horzLines: { color: withAlpha(t.border, 0.6) } },
    rightPriceScale: { borderColor: t.border },
    timeScale: { borderColor: t.border },
    crosshair: { vertLine: { labelBackgroundColor: t.primary }, horzLine: { labelBackgroundColor: t.primary } },
  };
}

// The candles as chart data (times converted, sorted, duplicates dropped).
function prepare(candles) {
  const seen = new Set();
  return candles.map((c) => ({ ...c, time: toChartTime(c.time) }))
    .filter((c) => (seen.has(c.time) ? false : seen.add(c.time)))
    .sort((a, b) => (a.time > b.time ? 1 : -1));
}

export default function PriceChart({ candles = [], mode = "candles", overlays = [], markers = [], height = 420,
  showVolume = true, title }) {
  const box = useRef(null);
  const chart = useRef(null);                       // { api, lib, series: [], markers }
  const [ready, setReady] = useState(false);
  const [hover, setHover] = useState(null);
  const [range, setRange] = useState("All");
  const { resolvedTheme } = useTheme();
  const bars = useMemo(() => prepare(candles), [candles]);
  const intraday = typeof bars[0]?.time === "number";
  const ranges = intraday ? INTRADAY_RANGES : DAILY_RANGES;

  // Create the chart once (the library is only downloaded in the browser), and remove it when leaving.
  useEffect(() => {
    let gone = false;
    import("lightweight-charts").then((lib) => {
      if (gone || !box.current) return;
      const api = lib.createChart(box.current, { autoSize: true, ...themeOptions(readChartTheme()),
        localization: { timeFormatter: chartTimeLabel, priceFormatter: (p) => formatNumber(p, 2) },
        timeScale: { timeVisible: true, secondsVisible: false } });
      chart.current = { api, lib, series: [] };
      setReady(true);
    });
    return () => {
      gone = true;
      chart.current?.api.remove();
      chart.current = null;
    };
  }, []);

  // Follow the light / dark theme.
  useEffect(() => {
    if (ready) chart.current?.api.applyOptions(themeOptions(readChartTheme()));
  }, [ready, resolvedTheme]);

  // (Re)draw every series when the data, the mode or the theme changes.
  useEffect(() => {
    const c = chart.current;
    if (!ready || !c) return undefined;
    const t = readChartTheme();
    const { lib, api } = c;
    c.series.forEach((s) => api.removeSeries(s));
    c.series = [];
    const main = mode === "line"
      ? api.addSeries(lib.LineSeries, { color: t.primary, lineWidth: 2 })
      : api.addSeries(lib.CandlestickSeries, { upColor: t.profit, downColor: t.loss, borderUpColor: t.profit,
        borderDownColor: t.loss, wickUpColor: t.profit, wickDownColor: t.loss });
    main.setData(mode === "line" ? bars.map((b) => ({ time: b.time, value: b.close })) : bars);
    c.series.push(main);
    c.main = main;
    if (showVolume && bars.some((b) => b.volume)) {
      const volume = api.addSeries(lib.HistogramSeries, { priceScaleId: "volume", priceFormat: { type: "volume" },
        lastValueVisible: false, priceLineVisible: false });
      api.priceScale("volume").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
      volume.setData(bars.map((b) => ({ time: b.time, value: b.volume || 0,
        color: withAlpha(b.close >= b.open ? t.profit : t.loss, 0.45) })));
      c.series.push(volume);
      c.volume = volume;
    } else {
      c.volume = null;
    }
    c.lines = overlays.map((o, i) => {
      const line = api.addSeries(lib.LineSeries, { color: o.color || t.series[i % t.series.length], lineWidth: 2,
        lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false, title: o.name });
      line.setData(prepare((o.data || []).filter((p) => p.value !== null && p.value !== undefined)
        .map((p) => ({ time: p.time, value: Number(p.value) }))));
      c.series.push(line);
      return { name: o.name, series: line, color: o.color || t.series[i % t.series.length] };
    });
    lib.createSeriesMarkers(main, markers.map((m) => {
      const buy = String(m.side).toUpperCase() === "BUY";
      return { time: toChartTime(m.time), position: buy ? "belowBar" : "aboveBar", shape: buy ? "arrowUp" : "arrowDown",
        color: buy ? t.profit : t.loss, text: m.text ?? (buy ? "BUY" : "SELL") };
    }).sort((a, b) => (a.time > b.time ? 1 : -1)));
    api.timeScale().fitContent();
    return undefined;
  }, [ready, bars, mode, overlays, markers, showVolume, resolvedTheme]);

  // The read-out follows the crosshair (or shows the last candle).
  useEffect(() => {
    const c = chart.current;
    if (!ready || !c) return undefined;
    const move = (param) => {
      if (!param.time || !param.point) { setHover(null); return; }
      const bar = param.seriesData.get(c.main);
      const vol = c.volume ? param.seriesData.get(c.volume) : null;
      setHover({ time: param.time, bar, volume: vol?.value,
        lines: (c.lines || []).map((l) => ({ ...l, value: param.seriesData.get(l.series)?.value })) });
    };
    c.api.subscribeCrosshairMove(move);
    return () => c.api.unsubscribeCrosshairMove(move);
  }, [ready, bars, overlays]);

  // A range button: show the last N days (All = everything).
  const showRange = (label, days) => {
    setRange(label);
    const c = chart.current;
    if (!c || !bars.length) return;
    if (!days) { c.api.timeScale().fitContent(); return; }
    const last = chartTimeToDate(bars[bars.length - 1].time);
    const from = new Date(last.getTime() - days * 86400 * 1000);
    const fromTime = intraday ? Math.floor(from.getTime() / 1000) : from.toISOString().slice(0, 10);
    try {
      c.api.timeScale().setVisibleRange({ from: fromTime, to: bars[bars.length - 1].time });
    } catch {
      c.api.timeScale().fitContent();
    }
  };

  const shown = hover?.bar ? hover : bars.length ? { time: bars[bars.length - 1].time, bar: bars[bars.length - 1],
    volume: bars[bars.length - 1].volume, lines: [] } : null;
  const bar = shown?.bar;
  return (
    <div className="space-y-2 rounded-xl border bg-card p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        {title && <h3 className="text-sm font-semibold">{title}</h3>}
        <div className="ml-auto flex gap-1" role="group" aria-label="Range">
          {ranges.map(([label, days]) => (
            <Button key={label} size="xs" variant={range === label ? "secondary" : "ghost"} aria-pressed={range === label}
              onClick={() => showRange(label, days)}>{label}</Button>
          ))}
        </div>
      </div>
      <div className="num flex min-h-5 flex-wrap gap-x-3 text-xs text-muted-foreground" aria-live="off">
        {bar && (
          <>
            <span className="text-foreground">{chartTimeLabel(shown.time)}</span>
            {bar.open !== undefined ? (
              <>
                <span>O <b className="text-foreground">{formatNumber(bar.open)}</b></span>
                <span>H <b className="text-foreground">{formatNumber(bar.high)}</b></span>
                <span>L <b className="text-foreground">{formatNumber(bar.low)}</b></span>
                <span>C <b className={bar.close >= bar.open ? "text-profit" : "text-loss"}>{formatNumber(bar.close)}</b></span>
              </>
            ) : <span>Close <b className="text-foreground">{formatNumber(bar.value ?? bar.close)}</b></span>}
            {shown.volume !== undefined && shown.volume !== null && <span>Vol <b className="text-foreground">{formatInt(shown.volume)}</b></span>}
            {shown.lines?.filter((l) => l.value !== undefined).map((l) => (
              <span key={l.name}><span style={{ color: l.color }}>■</span> {l.name} {formatNumber(l.value)}</span>
            ))}
          </>
        )}
      </div>
      <div className="relative w-full" style={{ height }}>
        {!ready && <Skeleton className="absolute inset-0 rounded-lg" />}
        <div ref={box} className="absolute inset-0" />
      </div>
      {overlays.length > 0 && (
        <div className="flex flex-wrap gap-3 text-xs text-muted-foreground">
          {overlays.map((o, i) => (
            <span key={o.name} className="flex items-center gap-1">
              <span className="inline-block h-0.5 w-4 rounded" style={{ background: o.color || `var(--chart-${(i % 5) + 1})` }} />
              {o.name}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
