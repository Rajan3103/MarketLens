// A coloured dot with a word next to it (the word is always there, so colour is never the only sign).
//
//   <StatusDot status="ok" label="Scheduler running" />     status: ok (green) | warn (amber) | error (red) | off (grey)
const DOT = { ok: "bg-profit", warn: "bg-warning", error: "bg-loss", off: "bg-muted-foreground" };
const WORD = { ok: "OK", warn: "Warning", error: "Problem", off: "Off" };

export default function StatusDot({ status = "off", label, pulse = false, className = "" }) {
  const dot = DOT[status] || DOT.off;
  return (
    <span className={`inline-flex items-center gap-2 text-sm ${className}`}>
      <span className="relative flex size-2.5" aria-hidden="true">
        {pulse && <span className={`absolute inline-flex size-full animate-ping rounded-full opacity-60 ${dot}`} />}
        <span className={`relative inline-flex size-2.5 rounded-full ${dot}`} />
      </span>
      <span>{label || WORD[status] || status}</span>
    </span>
  );
}
