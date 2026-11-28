import { proxyJson } from "@/lib/backend"

/**
 * Proxy hybrid real-estate search requests from the frontend origin to FastAPI ``POST /v1/search``.
 * @param request - Incoming POST request carrying a ``SearchRequest`` JSON body.
 * @returns Upstream JSON response envelope with ranked listings and search metadata.
 */
export async function POST(request: Request) {
  return proxyJson(request, "/v1/search")
}
