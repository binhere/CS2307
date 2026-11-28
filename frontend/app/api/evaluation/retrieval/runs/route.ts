import { proxyJson } from "@/lib/backend"

/**
 * Proxy GET requests for retrieval evaluation history and latest 4-mode results.
 * @param request - Incoming GET request (forwards optional ?limit= query parameter).
 * @returns Upstream JSON envelope from FastAPI GET /v1/evaluation/retrieval/runs.
 */
export async function GET(request: Request) {
  const { search } = new URL(request.url)
  return proxyJson(request, `/v1/evaluation/retrieval/runs${search}`)
}
