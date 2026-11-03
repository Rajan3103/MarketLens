// A label (with its unit, e.g. "Capital (Rs)") above one input, plus an optional hint below it.
export default function FormField({ label, unit, hint, htmlFor, children, className = "" }) {
  return (
    <div className={`flex flex-col gap-1 ${className}`}>
      <label htmlFor={htmlFor} className="text-sm font-medium text-slate-700 dark:text-slate-300">
        {label}{unit && <span className="font-normal text-slate-500"> ({unit})</span>}
      </label>
      {children}
      {hint && <p className="text-xs text-slate-500">{hint}</p>}
    </div>
  );
}

// The same look for every text box and drop-down.
export const INPUT_CLASS = "rounded-md border border-slate-300 bg-white px-3 py-2 text-sm focus:border-blue-500 " +
  "focus:outline-none focus:ring-2 focus:ring-blue-200 dark:border-slate-700 dark:bg-slate-900";
