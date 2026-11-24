// Client-side JSON fetching shared by every component that calls the app/api/* proxies,
// so error bodies are read the same way everywhere.

/**
 * Response envelope the FastAPI backend wraps every payload in.
 * `data` holds the payload on success; `errors[0].detail` carries the message on failure.
 */
export interface ApiEnvelope<T> {
  data?: T | null
  errors?: Array<{ loc?: string | null; msg?: string | null; detail?: string | null }>
  status?: string
}

/**
 * Error thrown by fetchJson for a non-2xx response. Keeps the HTTP status so callers can
 * tell "not found" apart from other failures, and the parsed body for callers that need
 * more than the message.
 */
export class HttpError extends Error {
  readonly status: number
  readonly payload: unknown

  /**
   * @param message - human-readable message, usually from extractError.
   * @param status - HTTP status code of the failed response.
   * @param payload - parsed JSON body of the response, or null when it had none.
   */
  constructor(message: string, status: number, payload: unknown) {
    super(message)
    this.name = "HttpError"
    this.status = status
    this.payload = payload
  }
}

/**
 * Read the message the backend itself wrote into its error envelope.
 * @param payload - parsed JSON body; only `{ errors: [{ detail: string }] }` is recognised.
 * @returns the first non-empty `errors[].detail`, or null when the body is not that envelope.
 */
export function readEnvelopeDetail(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null
  const errors = (payload as { errors?: unknown }).errors
  if (!Array.isArray(errors)) return null
  const detail = (errors[0] as { detail?: unknown } | undefined)?.detail
  return typeof detail === "string" && detail.trim() !== "" ? detail : null
}

/**
 * Read the machine-readable error code the backend put in its error envelope, e.g.
 * "LLM_KEY_INVALID", so callers can react to a specific failure rather than its wording.
 * @param payload - parsed JSON body; only `{ errors: [{ msg: string }] }` is recognised.
 * @returns the first non-empty `errors[].msg`, or null when the body is not that envelope.
 */
export function readEnvelopeCode(payload: unknown): string | null {
  if (!payload || typeof payload !== "object") return null
  const errors = (payload as { errors?: unknown }).errors
  if (!Array.isArray(errors)) return null
  const code = (errors[0] as { msg?: unknown } | undefined)?.msg
  return typeof code === "string" && code.trim() !== "" ? code : null
}

/**
 * Pick the most specific human-readable message out of an error body.
 * The backend envelope is checked first, then a bare FastAPI `{ detail }`, then the
 * proxy's own `{ error }` (network failures and non-JSON upstream bodies).
 * @param payload - parsed JSON body of any shape (null, string and arrays are tolerated).
 * @param fallback - message returned when the body carries none.
 * @returns the extracted message, or `fallback`.
 */
export function extractError(payload: unknown, fallback: string): string {
  const envelopeDetail = readEnvelopeDetail(payload)
  if (envelopeDetail) return envelopeDetail
  if (!payload || typeof payload !== "object") return fallback
  const body = payload as { detail?: unknown; error?: unknown }
  for (const candidate of [body.detail, body.error]) {
    if (typeof candidate === "string" && candidate.trim() !== "") return candidate
  }
  return fallback
}

/**
 * Tell an aborted fetch apart from a real failure, so cancelled requests stay silent.
 * @param error - any value caught from a fetch chain.
 * @returns true when the error is the DOMException fetch throws on abort.
 */
export function isAbortError(error: unknown): boolean {
  return error instanceof DOMException && error.name === "AbortError"
}

/**
 * Fetch a URL and parse its JSON body, throwing for non-2xx responses.
 * @param url - request URL, usually a same-origin /api/* proxy route.
 * @param init - standard fetch options; pass `signal` to make the request abortable.
 * @param fallbackMessage - message used when a failed response carries none;
 *   defaults to "API Error: <status>".
 * @returns the parsed JSON body cast to `T` (null for an empty body).
 * @throws HttpError with the status and extracted message for non-2xx responses or a
 *   2xx body that is not JSON; network failures and aborts propagate unchanged from fetch.
 */
export async function fetchJson<T = unknown>(
  url: string,
  init?: RequestInit,
  fallbackMessage?: string,
): Promise<T> {
  const response = await fetch(url, init)
  const text = await response.text()

  let payload: unknown = null
  let isJsonBody = true
  if (text) {
    try {
      payload = JSON.parse(text)
    } catch {
      isJsonBody = false
    }
  }

  if (!response.ok) {
    const message = extractError(payload, fallbackMessage ?? `API Error: ${response.status}`)
    throw new HttpError(message, response.status, payload)
  }
  if (!isJsonBody) {
    throw new HttpError(fallbackMessage ?? "Invalid JSON response", response.status, null)
  }
  return payload as T
}
