"use client";
// Draws a chart the Python code made (Plotly figure JSON from the API: {data, layout}) in the app's theme:
// see-through background, the app's font, grid and text colours from the theme, and red - grey - green colour
// scales centred at 0 for heatmaps (so a loss is always red and a profit green). No Plotly logo bar; it fills
// the width of its card and redraws when the theme changes.
//
//   <PlotlyChart figure={answer.chart} height={520} />
//
// Plotly only works in the browser, so it is loaded with next/dynamic and ssr: false (only on pages that use it).
import dynamic from "next/dynamic";
import { useTheme } from "next-themes";
import { useMemo } from "react";

import Loading from "@/components/Loading";
import { readChartTheme } from "@/lib/chartTheme";

const Plot = dynamic(async () => {
  const [{ default: Plotly }, { default: createPlotlyComponent }] = await Promise.all([
    import("plotly.js-dist-min"),
    import("react-plotly.js/factory"),
  ]);
  return createPlotlyComponent(Plotly);
}, { ssr: false, loading: () => <Loading text="Loading the chart…" variant="chart" /> });

const SCALED = new Set(["heatmap", "treemap"]);

// The theme's look for one axis (keeps whatever the figure set itself, e.g. tick format or range).
function themedAxis(axis = {}, t) {
  return { gridcolor: t.border, zerolinecolor: t.border, linecolor: t.border, ...axis,
    tickfont: { color: t.mutedForeground, ...(axis.tickfont || {}) },
    title: typeof axis.title === "string" ? { text: axis.title, font: { color: t.mutedForeground } }
      : { ...(axis.title || {}), font: { color: t.mutedForeground, ...(axis.title?.font || {}) } } };
}

// A red - grey - green scale centred at 0 for heatmaps and treemaps of profit / loss.
function themedTrace(trace, t, zero) {
  if (!SCALED.has(trace.type)) return trace;
  const scale = [[0, t.loss], [0.5, zero], [1, t.profit]];
  if (trace.type === "treemap") {
    return { ...trace, marker: { ...(trace.marker || {}), colorscale: scale, cmid: trace.marker?.cmid ?? 0 } };
  }
  return { ...trace, colorscale: scale, zmid: trace.zmid ?? 0, textfont: { color: t.foreground, ...(trace.textfont || {}) },
    colorbar: { ...(trace.colorbar || {}), tickfont: { color: t.mutedForeground },
      title: { ...(trace.colorbar?.title || {}), font: { color: t.mutedForeground } } } };
}

// The whole figure in the current theme.
export function themeFigure(figure, t) {
  const zero = t.input;
  const layout = { ...figure.layout };
  Object.keys(layout).filter((k) => /^[xy]axis\d*$/.test(k)).forEach((k) => { layout[k] = themedAxis(layout[k], t); });
  if (!layout.xaxis) layout.xaxis = themedAxis({}, t);
  if (!layout.yaxis) layout.yaxis = themedAxis({}, t);
  const title = typeof layout.title === "string" ? { text: layout.title } : layout.title;
  return {
    data: (figure.data || []).map((trace) => themedTrace(trace, t, zero)),
    layout: {
      ...layout,
      ...(title ? { title: { ...title, font: { color: t.foreground, size: 15, ...(title.font || {}) } } } : {}),
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { ...(layout.font || {}), family: t.font, color: t.foreground },
      legend: { ...(layout.legend || {}), font: { color: t.mutedForeground } },
      hoverlabel: { bgcolor: t.popover, bordercolor: t.border, font: { color: t.foreground, family: t.font } },
    },
  };
}

export default function PlotlyChart({ figure, height, className = "" }) {
  const { resolvedTheme } = useTheme();
  // read the theme again whenever it changes (the colours live in CSS variables)
  const themed = useMemo(() => (figure?.data ? themeFigure(figure, readChartTheme()) : null),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- resolvedTheme is the signal to read the colours again
    [figure, resolvedTheme]);
  if (!themed) return null;
  const layout = { ...themed.layout, autosize: true, ...(height ? { height } : {}) };
  return (
    <div className={`w-full overflow-hidden rounded-xl border bg-card p-2 ${className}`}>
      <Plot
        data={themed.data}
        layout={layout}
        config={{ responsive: true, displaylogo: false, displayModeBar: false }}
        useResizeHandler
        style={{ width: "100%", height: layout.height || 450 }}
      />
    </div>
  );
}
