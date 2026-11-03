"use client";
// Draws a chart the Python code made (Plotly figure JSON from the API: {data, layout}).
//
//   <PlotlyChart figure={answer.chart} height={520} />
//
// Plotly only works in the browser, so it is loaded with next/dynamic and ssr: false (and only on pages
// that show a chart, keeping the others fast).
import dynamic from "next/dynamic";

import Loading from "@/components/Loading";

const Plot = dynamic(async () => {
  const [{ default: Plotly }, { default: createPlotlyComponent }] = await Promise.all([
    import("plotly.js-dist-min"),
    import("react-plotly.js/factory"),
  ]);
  return createPlotlyComponent(Plotly);
}, { ssr: false, loading: () => <Loading text="Loading the chart…" /> });

export default function PlotlyChart({ figure, height, className = "" }) {
  if (!figure?.data) return null;
  const layout = { ...figure.layout, autosize: true, ...(height ? { height } : {}) };
  return (
    <div className={`w-full overflow-hidden rounded-lg border border-slate-200 dark:border-slate-800 ${className}`}>
      <Plot
        data={figure.data}
        layout={layout}
        config={{ responsive: true, displaylogo: false, modeBarButtonsToRemove: ["lasso2d", "select2d"] }}
        useResizeHandler
        style={{ width: "100%", height: layout.height || 450 }}
      />
    </div>
  );
}
