import { proxyJson } from "@/lib/backend"

/**
 * Proxy POST requests to trigger an in-process retrieval evaluation run.
 * @param request - Incoming authenticated POST request with optional retrieval_mode payload.
 * @returns Upstream JSON envelope from FastAPI POST /v1/evaluation/retrieval/run.
 */
export async function POST(request: Request) {
  return proxyJson(request, "/v1/evaluation/retrieval/run")
}
