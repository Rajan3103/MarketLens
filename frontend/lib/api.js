// Talk to the MarketLens backend API (python -m marketlens.api).
//
//   const health = await apiGet("health");                 // GET /api/health
//   const prices = await apiGet("market/history?symbol=RELIANCE.NS");
//   await apiPost("strategies", { name: "Straddle" });     // POST /api/strategies
//
// The browser only talks to this web app (localhost:3000); next.config.mjs forwards /api/... to the
// Python server. Every problem becomes an ApiError with a readable message, so pages can just show it.

export const NOT_RUNNING = "Cannot reach the MarketLens API. Is python -m marketlens.api running?";
export const TIMEOUT_SECONDS = 20;

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

// Send one request and return the JSON answer, or throw an ApiError.
async function request(method, path, body) {
  let response;
  const timer = new AbortController();
  const stop = setTimeout(() => timer.abort(), TIMEOUT_SECONDS * 1000);
  try {
    response = await fetch(apiUrl(path), {
      signal: timer.signal,
      method,
      credentials: "include",                     // send cookies (for a login later)
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
