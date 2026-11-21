// Talk to the MarketLens backend API (python -m marketlens.api).
//
//   const health = await apiGet("health");                 // GET /api/health
//   const prices = await apiGet("market/history?symbol=RELIANCE.NS");
//   await apiPost("strategies", { name: "Straddle" });     // POST /api/strategies
//
// The browser only talks to this web app (localhost:3000); next.config.mjs forwards /api/... to the
// Python server. Every problem becomes an ApiError with a readable message, so pages can just show it.
// A 401 answer (DASHBOARD_PASSWORD is set and you are not logged in) sends the browser to /login, which
// comes back to the same page after logging in. Pages usually call these through lib/queries.js.

export const NOT_RUNNING = "Cannot reach the MarketLens API. Is python -m marketlens.api running?";
export const TIMEOUT_SECONDS = 20;
export const LOGIN_PAGE = "/login";

// An error with a sentence a person can read, plus the HTTP status (0 = no answer at all).
export class ApiError extends Error {
  constructor(message, status = 0) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

// "health", "/health" or "/api/health" all become "/api/health".
export function apiUrl(path) {
  const clean = String(path).replace(/^\/+/, "");
  return clean.startsWith("api/") ? `/${clean}` : `/api/${clean}`;
}

// Go to the login page, remembering where to come back to. Not for /api/auth/* answers (the login page asks
// those itself) and not when already on the login page.
export function goToLogin(path = "") {
  if (typeof window === "undefined") return;
  if (apiUrl(path).startsWith("/api/auth/")) return;
  if (window.location.pathname === LOGIN_PAGE) return;
  const back = encodeURIComponent(window.location.pathname + window.location.search);
  // a full page load on purpose: this runs outside React, and it clears every cached answer of the old session
  // eslint-disable-next-line @next/next/no-location-assign-relative-destination
  window.location.assign(`${LOGIN_PAGE}?next=${back}`);
}

// Send one request and return the JSON answer, or throw an ApiError.
async function request(method, path, body) {
  let response;
  const timer = new AbortController();
  const stop = setTimeout(() => timer.abort(), TIMEOUT_SECONDS * 1000);
  try {
    response = await fetch(apiUrl(path), {
      signal: timer.signal,
      method,
      credentials: "include",                     // send the login cookie
      headers: body === undefined ? { Accept: "application/json" }
        : { Accept: "application/json", "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      cache: "no-store",
    });
  } catch (err) {
    if (err.name === "AbortError") {
      throw new ApiError(`The MarketLens API did not answer within ${TIMEOUT_SECONDS} seconds. Try again.`, 0);
    }
    throw new ApiError(NOT_RUNNING, 0);           // no network answer at all
  } finally {
    clearTimeout(stop);
  }
  let data = null;
  try {
    data = await response.json();
  } catch {
    data = null;                                  // not JSON (e.g. the proxy's own error page)
  }
  if (response.ok && data !== null) return data;
  if (response.status === 401) {
    goToLogin(path);
    throw new ApiError(data?.error || "Please log in first.", 401);
  }
  if (data && typeof data.error === "string") throw new ApiError(data.error, response.status);
  if (response.status >= 500 || data === null) throw new ApiError(NOT_RUNNING, response.status);
  throw new ApiError(`The API answered ${response.status} without an explanation.`, response.status);
}

// GET /api/<path>
export function apiGet(path) {
  return request("GET", path);
}

// POST /api/<path> with a JSON body
export function apiPost(path, body = {}) {
  return request("POST", path, body);
}
