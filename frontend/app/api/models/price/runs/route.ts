import { proxyJson } from "@/lib/backend"

/**
 * Proxy for the public history of price model training runs.
 * @param request - incoming request; an optional `limit` query parameter is passed on.
 * @returns the backend's envelope ({ runs, schedule_hour, new_data_share, is_running }) and status.
 */
export async function GET(request: Request) {
  const limit = new URL(request.url).searchParams.get("limit")
  return proxyJson(request, `/v1/models/price/runs${limit ? `?limit=${encodeURIComponent(limit)}` : ""}`)
}
