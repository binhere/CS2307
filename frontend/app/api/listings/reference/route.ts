import { proxyJson } from "@/lib/backend"

/**
 * Proxy for the listing form's choices: wards, former districts and categories.
 * @param request - incoming request; no query parameters are used.
 * @returns the backend's envelope and status.
 */
export async function GET(request: Request) {
  return proxyJson(request, "/v1/listings/reference")
}
