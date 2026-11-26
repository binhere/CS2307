import { proxyJson } from "@/lib/backend"

/**
 * Proxy for checking listings before posting.
 * @param request - incoming request: {"listings": [...]} and the user's bearer token.
 * @returns the backend's envelope (one report per listing) and status.
 */
export async function POST(request: Request) {
  return proxyJson(request, "/v1/listings/validate")
}
