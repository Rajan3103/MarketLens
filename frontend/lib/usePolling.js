"use client";
// Repeat a request every N seconds, but only while the page is open AND visible (kept for older pages; new
// code uses useApi from lib/queries.js, which this is now built on).
//
//   const { data, error, loading, refresh } = usePolling(() => apiGet("health"), 60);
//   usePolling(() => apiGet(`market/history?symbol=${s}`), 60, { key: s, enabled: marketOpen });
//
// - Asks once at the start, again whenever `key` changes (e.g. a new ticker), and every `seconds` while
//   `enabled` (seconds = 0 or null: never repeats). The first answer is always fetched, as before.
// - Hidden tab: no repeats; coming back asks at once. The old answer stays on screen while a new one loads.
import { useId } from "react";

import { useRequest } from "@/lib/queries";

export default function usePolling(request, seconds, { enabled = true, key = "" } = {}) {
  const id = useId();                              // this component's own cache entry
  return useRequest([id, key], request, { refreshSeconds: seconds || 0, repeat: enabled });
}
