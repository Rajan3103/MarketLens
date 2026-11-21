"use client";
// The data layer: TanStack Query on top of lib/api.js. Pages use these hooks instead of fetching by hand.
//
//   const health = useApi("health", { refreshSeconds: 30 });          // { data, error, loading, refresh, ... }
//   const save = useApiMutation("momentum/strategies", { success: "Saved" });   save.mutate({ name })
//   const job = useJob(jobId, { onDone: (j) => setResult(j.result) });  // follows /api/jobs/{id} every second
//
// - Repeats only while the browser tab is visible (TanStack Query pauses in hidden tabs).
// - While a new answer is on its way, the old one stays on screen (no flicker).
// - A 401 goes to /login (lib/api.js); 4xx answers are not retried, a lost connection is retried once.
import { keepPreviousData, QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { toast } from "sonner";

import { apiGet, apiPost } from "@/lib/api";

// A new QueryClient with the app's defaults (one per browser tab, made in components/QueryProvider.jsx).
export function makeQueryClient() {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 5_000,                         // two components asking within 5 s share one request
        retry: (count, error) => (error?.status === 0 || error?.status >= 500) && count < 1,
        refetchOnWindowFocus: true,               // coming back to the tab asks again
        refetchIntervalInBackground: false,       // never repeat in a hidden tab
      },
      mutations: { retry: false },
    },
  });
}

// The query's fields plus the names older code uses: loading (any request running) and refresh().
function withShortNames(query) {
  return { ...query, loading: query.isFetching, refresh: () => query.refetch() };
}

// GET /api/<path>, again every `refreshSeconds` while the tab is visible (0 = only once). path null = wait.
export function useApi(path, { refreshSeconds = 0, enabled = true, select } = {}) {
  const query = useQuery({
    queryKey: ["api", path],
    queryFn: () => apiGet(path),
    enabled: Boolean(path) && enabled,
    refetchInterval: refreshSeconds > 0 ? refreshSeconds * 1000 : false,
    placeholderData: keepPreviousData,            // keep showing the old answer while a new one loads
    select,
  });
  return withShortNames(query);
}

// Any request function on a timer (used by lib/usePolling.js for older pages). `id` keeps callers apart.
export function useRequest(id, request, { refreshSeconds = 0, repeat = true } = {}) {
  const requestRef = useRef(request);
  useEffect(() => { requestRef.current = request; });
  const query = useQuery({
    queryKey: ["request", ...[].concat(id)],
    queryFn: () => requestRef.current(),
    refetchInterval: repeat && refreshSeconds > 0 ? refreshSeconds * 1000 : false,
    placeholderData: keepPreviousData,
    staleTime: 0,
  });
  return withShortNames(query);
}

// POST /api/<path> (or a path made from the variables) with a toast when it worked or failed.
//   success: text, or (answer) => text; error: false to show no toast. invalidate: query keys to ask again.
export function useApiMutation(path, { success = "Done", error = true, onSuccess, onError, invalidate = [] } = {}) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (body = {}) => apiPost(typeof path === "function" ? path(body) : path, body),
    onSuccess: (answer, body) => {
      const text = typeof success === "function" ? success(answer) : success;
      if (text) toast.success(text);
      invalidate.forEach((key) => client.invalidateQueries({ queryKey: ["api", key] }));
      onSuccess?.(answer, body);
    },
    onError: (err, body) => {
      if (error) toast.error(err.message);
      onError?.(err, body);
    },
  });
}

// Follow one background job every second until it finishes; onDone(job) is called once at the end.
export function useJob(jobId, { onDone } = {}) {
  const told = useRef(null);
  const query = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => apiGet(`jobs/${jobId}`),
    enabled: Boolean(jobId),
    refetchInterval: (q) => (q.state.data?.finished ? false : 1000),
    refetchIntervalInBackground: true,            // a long backtest keeps going in a hidden tab
    staleTime: 0,
  });
  const job = query.data;
  useEffect(() => {
    if (job?.finished && told.current !== jobId) {
      told.current = jobId;
      onDone?.(job);
    }
  }, [job, jobId, onDone]);
  return { ...withShortNames(query), job, cancel: () => apiPost(`jobs/${jobId}/cancel`) };
}
