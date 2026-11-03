// A small "please wait" line with a spinner.
export default function Loading({ text = "Loading…" }) {
  return (
    <p role="status" className="flex items-center gap-2 py-4 text-sm text-slate-500">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-slate-300 border-t-blue-600" aria-hidden="true" />
      {text}
    </p>
  );
}
