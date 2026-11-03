"use client";
// A button for things that cannot be undone: tick the box, click once (the button asks again), click again.
// The second click must come within 8 seconds, otherwise it goes back to the start.
//
//   <DangerButton tick="Yes, block all new orders today" label="Kill switch ON" onConfirm={() => apiPost(...)} />
import { useEffect, useState } from "react";

const ARM_SECONDS = 8;

export default function DangerButton({ tick, label, onConfirm, disabled = false }) {
  const [sure, setSure] = useState(false);
  const [armed, setArmed] = useState(false);
  const [busy, setBusy] = useState(false);

  // Forget the first click after a few seconds.
  useEffect(() => {
    if (!armed) return undefined;
    const timer = setTimeout(() => setArmed(false), ARM_SECONDS * 1000);
    return () => clearTimeout(timer);
  }, [armed]);

  // First click arms the button; the second one does it.
  const click = async () => {
    if (!armed) {
      setArmed(true);
      return;
    }
    setBusy(true);
    try {
      await onConfirm();
    } finally {
      setBusy(false);
      setArmed(false);
      setSure(false);
    }
  };

  return (
    <div className="space-y-2">
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={sure} disabled={disabled}
          onChange={(e) => { setSure(e.target.checked); setArmed(false); }} />
        {tick}
      </label>
      <button onClick={click} disabled={!sure || disabled || busy}
        className={`rounded-md px-4 py-2 text-sm font-semibold text-white disabled:cursor-not-allowed disabled:opacity-40 ${armed
          ? "animate-pulse bg-red-800 ring-2 ring-red-300" : "bg-red-600 hover:bg-red-700"}`}>
        {busy ? "Working…" : armed ? `Click again to confirm: ${label}` : label}
      </button>
    </div>
  );
}
