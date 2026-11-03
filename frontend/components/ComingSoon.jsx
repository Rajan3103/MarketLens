// The page of a module that is not built in the web app yet: what it will do, and where to use it today.
import { STREAMLIT_STATUS, moduleFor } from "@/lib/modules";

export default function ComingSoon({ href }) {
  const mod = moduleFor(href);
  if (!mod) return <p>Unknown page.</p>;
  return (
    <section className="space-y-4">
      <h1 className="text-2xl font-bold">
        <span aria-hidden="true">{mod.icon}</span> {mod.num}. {mod.title}
      </h1>
      <p className="text-slate-600 dark:text-slate-400">{mod.description}</p>
      <div className="rounded-lg border border-amber-300 bg-amber-50 p-4 dark:border-amber-800 dark:bg-amber-950/40">
        <p className="font-semibold">🚧 Coming soon to the web app. This page will:</p>
        <ul className="mt-2 list-disc space-y-1 pl-6">
          {mod.planned.map((item) => <li key={item}>{item}</li>)}
        </ul>
      </div>
      {mod.streamlit !== "planned" && (
        <p className="text-sm text-slate-600 dark:text-slate-400">
          {STREAMLIT_STATUS[mod.streamlit]}: run <code className="rounded bg-slate-100 px-1 dark:bg-slate-800">
            streamlit run app.py</code> and pick module {mod.num} in its menu.
        </p>
      )}
    </section>
  );
}
