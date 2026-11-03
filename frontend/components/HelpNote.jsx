// A short explanation that opens when clicked ("What does this mean?").
export default function HelpNote({ title = "What does this mean?", children, open = false }) {
  return (
    <details open={open} className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-2 text-sm
      dark:border-slate-800 dark:bg-slate-900">
      <summary className="cursor-pointer font-medium text-slate-700 dark:text-slate-300">ℹ️ {title}</summary>
      <div className="space-y-2 pt-2 text-slate-600 dark:text-slate-400">{children}</div>
    </details>
  );
}
