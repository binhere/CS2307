import { proxyMultipart } from "@/lib/backend"

/**
 * Proxy for posting a listing: multipart with the listing JSON and its photos in two sizes.
 * @param request - incoming request with the user's bearer token.
 * @returns the backend's envelope and status (201 on success).
 */
export async function POST(request: Request) {
  return proxyMultipart(request, "/v1/listings")
}
