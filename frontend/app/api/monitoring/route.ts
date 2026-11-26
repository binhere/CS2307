import { proxyJson } from "@/lib/backend"

/**
 * Proxy for the public data dashboard API (aggregate numbers only).
 * @param request - incoming request; no query parameters are used.
 * @returns the backend's envelope and status.
 */
export async function GET(request: Request) {
  return proxyJson(request, "/v1/monitoring")
}
