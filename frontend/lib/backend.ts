// Single source of truth for the FastAPI backend base URL used by all
// app/api/* proxy routes (avoids re-declaring BACKEND_URL in each file).
export const BACKEND_URL = process.env.BACKEND_URL || "http://127.0.0.1:8001"

/**
 * Per-request LLM credentials the browser attaches to AI calls (bring-your-own-key).
 * Only these exact names are forwarded; their values are secrets and are never logged.
 */
const FORWARDED_LLM_HEADERS = ["X-LLM-Provider", "X-LLM-Api-Key", "X-LLM-Model", "X-LLM-Base-Url"] as const

/**
 * Build the headers a proxy route sends upstream: JSON content type, the caller's bearer
 * token for authenticated calls, and the X-LLM-* headers so the backend can use the
 * user's own LLM key for this request only.
 * @param request - the incoming route-handler request.
 * @returns headers for the backend fetch; absent caller headers are omitted.
 */
export function forwardAuthHeaders(request: Request): HeadersInit {
  const headers: Record<string, string> = { "Content-Type": "application/json" }
  const auth = request.headers.get("authorization")
  if (auth) headers["Authorization"] = auth
  for (const headerName of FORWARDED_LLM_HEADERS) {
    const value = request.headers.get(headerName)
    if (value) headers[headerName] = value
  }
  return headers
}

/**
 * Turn an upstream response body into a JSON-serialisable value without throwing.
 * FastAPI answers unhandled exceptions with a plain-text "Internal Server Error", which
 * must not be mistaken for a proxy failure.
 * @param text - raw upstream body, possibly empty or not JSON.
 * @returns the parsed JSON, null for an empty body, or `{ error: text }` for non-JSON text.
 */
function parseUpstreamBody(text: string): unknown {
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return { error: text }
  }
}

/**
 * Shared proxy for every app/api/* route: forwards method, auth and body to the backend
 * and passes the backend's body and status straight back.
 * @param request - the incoming route-handler request.
 * @param backendPath - backend path including any query string, e.g. "/v1/properties?page=2".
 * @param opts - `method` overrides the incoming request's method.
 * @returns a JSON Response with the upstream status; `{ error }` with 502 when the backend
 *   is unreachable.
 */
export async function proxyJson(
  request: Request,
  backendPath: string,
  opts: { method?: string } = {},
): Promise<Response> {
  const method = (opts.method || request.method || "GET").toUpperCase()
  const init: RequestInit = {
    method,
    headers: forwardAuthHeaders(request),
    cache: "no-store",
  }
  if (method !== "GET" && method !== "HEAD") {
    init.body = await request.text()
  } else {
    // A browser that drops a read (e.g. a superseded listing search) should stop the
    // backend work too. Writes such as chat messages are left to finish so they are saved.
    init.signal = request.signal
  }
  try {
    const upstream = await fetch(`${BACKEND_URL}${backendPath}`, init)
    const text = await upstream.text()
    return Response.json(parseUpstreamBody(text), { status: upstream.status })
  } catch (error) {
    return Response.json(
      { error: error instanceof Error ? error.message : "Upstream request failed" },
      { status: 502 },
    )
  }
}
