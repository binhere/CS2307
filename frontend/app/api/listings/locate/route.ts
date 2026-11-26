import { proxyJson } from "@/lib/backend"

/**
 * Proxy for the form's map: the ward and former districts of a picked point.
 * @param request - incoming request with latitude and longitude query parameters.
 * @returns the backend's envelope and status.
 */
export async function GET(request: Request) {
  const query = new URL(request.url).searchParams
  const parameters = new URLSearchParams({ latitude: query.get("latitude") ?? "", longitude: query.get("longitude") ?? "" })
  return proxyJson(request, `/v1/listings/locate?${parameters}`)
}
