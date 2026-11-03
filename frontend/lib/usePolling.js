"use client";
// Repeat a request every N seconds, but only while the page is open AND visible.
//
//   const { data, error, loading, refresh } = usePolling(() => apiGet("health"), 60);
//   usePolling(() => apiGet(`market/history?symbol=${s}`), 60, { key: s, enabled: marketOpen });
//
// - Asks once at the start, again whenever `key` changes (e.g. a new ticker), and every `seconds` while
//   `enabled` (seconds = 0 or null: never repeats).
// - When the tab is hidden (another tab, minimised window) it stops asking; when you come back it asks at
//   once and starts the timer again. Leaving the page stops it for good.
// - The timer never starts a second request while one is still running, and an old answer never
//   replaces a newer one. The request function may be written inline; the newest one is always used.
import { useCallback, useEffect, useRef, useState } from "react";

export default function usePolling(request, seconds, { enabled = true, key = "" } = {}) {
  const [state, setState] = useState({ data: null, error: null, loading: true });
  const requestRef = useRef(request);
  const running = useRef(0);
  const latest = useRef(0);

  // Always call the newest request function (it may use new page settings).
  useEffect(() => { requestRef.current = request; });

  // Ask now. force=false (the timer) skips while a request is running; newer answers always win.
  const ask = useCallback(async (force = true) => {
    if (!force && running.current > 0) return;
    running.current += 1;
    const mine = ++latest.current;
    await Promise.resolve();                       // let React finish the current render first
    if (mine === latest.current) setState((s) => ({ ...s, loading: true }));
    try {
      const data = await requestRef.current();
      if (mine === latest.current) setState({ data, error: null, loading: false });
    } catch (error) {
      if (mine === latest.current) setState((s) => ({ ...s, error, loading: false }));
    } finally {
      running.current -= 1;
    }
  }, []);

  // The first answer, and again when the key changes.
  useEffect(() => { ask(true); }, [key, ask]);

  // The timer: only while enabled and the page is visible.
  useEffect(() => {
    if (!enabled || !seconds) return undefined;
    let timer = null;
    const start = () => { if (timer === null) timer = setInterval(() => ask(false), seconds * 1000); };
    const stop = () => { if (timer !== null) { clearInterval(timer); timer = null; } };
    const onVisibility = () => {
      if (document.visibilityState === "visible") { ask(false); start(); } else { stop(); }
    };
    if (document.visibilityState === "visible") start();
    document.addEventListener("visibilitychange", onVisibility);
    return () => { stop(); document.removeEventListener("visibilitychange", onVisibility); };
  }, [enabled, seconds, ask]);

  return { ...state, refresh: () => ask(true) };
}
