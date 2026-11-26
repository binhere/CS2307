import { proxyJson } from "@/lib/backend"

/**
 * Proxy for "Train ngay": starts a price model training run for the signed-in user.
 * @param request - incoming request with the user's bearer token.
 * @returns the backend's envelope: 202 with the run id, 409 while another run is going, 401 when signed out.
 */
export async function POST(request: Request) {
  return proxyJson(request, "/v1/models/price/train")
}
