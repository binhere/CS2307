"use client"

// The "Đánh giá tìm kiếm" tab on /du-lieu: compares the four retrieval strategies
// (PostgreSQL FTS + SQL, pgvector only, Hybrid, Hybrid + Graph) on the 150-query benchmark,
// lets signed-in users trigger a new background run, and polls live progress while running.

import { useEffect, useState } from "react"
import Link from "next/link"
import { Loader2, Play } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { authHeaders, useAuth } from "@/lib/auth-context"
import { HttpError, extractError, fetchJson, isAbortError, type ApiEnvelope } from "@/lib/http"
import { DataTable, StatusLabel } from "./charts"
import { formatMoment, formatNumber, formatShare } from "./format"

const LOAD_ERROR = "Không tải được kết quả đánh giá tìm kiếm. Vui lòng thử lại sau."
const REFRESH_WHILE_RUNNING_MS = 4000
const RUN_LIMIT = 20

type RequestMode = "all" | "fts" | "vector" | "hybrid" | "hybrid_graph"

interface LatencyMetrics {
  p50: number | null
  p95: number | null
  mean: number | null
}

interface ModeSummary {
  retrieval_mode: string
  top_k: number
  queries_total: number
  queries_succeeded: number
  queries_failed: number
  zero_result_rate: number | null
  semantic_enabled_rate: number | null
  graph_enabled_rate: number | null
  exact_constraint_match_at_5: number | null
  exact_constraint_match_at_10: number | null
  per_constraint_match_at_10: Record<string, number | null>
  exact_constraint_match_at_10_by_category: Record<string, number | null>
  latency_ms: LatencyMetrics
  run_id?: number
  finished_at?: string | null
}

interface RetrievalEvalRun {
  id: number
  retrieval_mode: string
  requested_by_name: string | null
  status: "running" | "completed" | "failed"
  queries_total: number
  queries_done: number
  started_at: string
  finished_at: string | null
  summary: {
    modes?: Record<string, ModeSummary>
    duration_seconds?: number
  } | null
  error: string | null
}

interface RetrievalEvalPayload {
  runs: RetrievalEvalRun[]
  latest_by_mode: Record<string, ModeSummary>
  is_running: boolean
  query_count: number
}

const MODE_ORDER = ["fts", "vector", "hybrid", "hybrid_graph"] as const

const MODE_LABELS: Record<string, string> = {
  all: "Cả 4 phương pháp (Ablation)",
  fts: "PostgreSQL FTS (ts_rank_cd) + SQL",
  vector: "pgvector Cosine Similarity",
  hybrid: "Hybrid (FTS + SQL + pgvector)",
  hybrid_graph: "Hybrid + Neo4j Graph (mặc định)",
}

const CONSTRAINT_LABELS: Record<string, string> = {
  property_type: "Loại bất động sản",
  price: "Khoảng giá",
  area: "Diện tích",
  bedrooms: "Số phòng ngủ",
  bathrooms: "Số phòng tắm",
  location: "Khu vực (phường/quận)",
  features: "Tiện ích & đặc điểm",
}

const CATEGORY_LABELS: Record<string, string> = {
  budget_apartment: "Căn hộ theo ngân sách (20)",
  budget_house: "Nhà đất theo ngân sách (20)",
  location_price: "Khu vực + khoảng giá (34)",
  amenity_graph: "Gần tiện ích / Graph OSM (24)",
  feature_specific: "Tiện nghi nội khu (20)",
  semantic_intent: "Ngữ nghĩa / văn nói (16)",
  complex_multi_constraint: "Đa ràng buộc 4–6 điều kiện (16)",
}

function formatOptionalShare(value: number | null | undefined): string {
  return typeof value === "number" ? formatShare(value) : "–"
}

function formatMs(value: number | null | undefined): string {
  return typeof value === "number" ? `${Math.round(value).toLocaleString("vi-VN")} ms` : "–"
}

/**
 * Retrieval evaluation dashboard tab: 4-mode comparison table, constraint/category
 * breakdown, run history, and the "Chạy đánh giá" trigger for signed-in users.
 * @returns The tab panel element.
 */
export function RetrievalEvaluation() {
  const { user } = useAuth()
  const [payload, setPayload] = useState<RetrievalEvalPayload | null>(null)
  const [selectedMode, setSelectedMode] = useState<RequestMode>("all")
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ text: string; isError: boolean } | null>(null)
  const [isStarting, setIsStarting] = useState(false)
  const [refreshCount, setRefreshCount] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    fetchJson<ApiEnvelope<RetrievalEvalPayload>>(
      `/api/evaluation/retrieval/runs?limit=${RUN_LIMIT}`,
      { signal: controller.signal },
      LOAD_ERROR,
    )
      .then((body) => {
        if (!body?.data) {
          setError(LOAD_ERROR)
          return
        }
        setError(null)
        setPayload(body.data)
      })
      .catch((reason: unknown) => {
        if (isAbortError(reason)) return
        setError(reason instanceof HttpError ? reason.message : LOAD_ERROR)
      })
    return () => controller.abort()
  }, [refreshCount])

  useEffect(() => {
    if (!payload?.is_running) return
    const timer = window.setTimeout(
      () => setRefreshCount((count) => count + 1),
      REFRESH_WHILE_RUNNING_MS,
    )
    return () => window.clearTimeout(timer)
  }, [payload])

  const handleRun = async () => {
    setIsStarting(true)
    setNotice(null)
    try {
      const response = await fetch("/api/evaluation/retrieval/run", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...authHeaders() },
        body: JSON.stringify({ retrieval_mode: selectedMode }),
      })
      const body = await response.json().catch(() => null)
      if (!response.ok) {
        setNotice({
          text: extractError(body, "Không khởi chạy được đánh giá. Vui lòng thử lại."),
          isError: true,
        })
      } else {
        setNotice({
          text: `Đã bắt đầu chạy đánh giá (${MODE_LABELS[selectedMode]}). Tiến độ sẽ tự cập nhật.`,
          isError: false,
        })
        setRefreshCount((count) => count + 1)
      }
    } catch {
      setNotice({ text: "Không kết nối được tới máy chủ.", isError: true })
    } finally {
      setIsStarting(false)
    }
  }

  if (error && !payload) {
    return (
      <div className="rounded-xl border border-border bg-card p-6 text-sm">
        <p className="text-destructive">{error}</p>
        <button
          type="button"
          onClick={() => setRefreshCount((count) => count + 1)}
          className="mt-3 rounded-md border border-border px-3 py-1.5 text-foreground"
        >
          Thử lại
        </button>
      </div>
    )
  }

  if (!payload) {
    return (
      <div className="space-y-4" aria-busy="true" aria-label="Đang tải đánh giá tìm kiếm">
        <Skeleton className="h-24 rounded-xl" />
        <Skeleton className="h-64 rounded-xl" />
      </div>
    )
  }

  const activeRun = payload.runs.find((run) => run.status === "running") ?? null
  const comparedModes = MODE_ORDER.filter((mode) => Boolean(payload.latest_by_mode[mode]))
  const primaryMode = payload.latest_by_mode.hybrid_graph
    ?? (comparedModes.length > 0 ? payload.latest_by_mode[comparedModes[comparedModes.length - 1]] : null)

  return (
    <div className="space-y-8">
      <section className="rounded-xl border border-border bg-card p-4 sm:p-5">
        <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:justify-between">
          <div>
            <h2 className="text-base font-semibold text-foreground">
              Đánh giá Retrieval trên bộ {formatNumber(payload.query_count)} câu truy vấn
            </h2>
            <p className="mt-1 text-sm text-muted-foreground">
              So sánh trực tiếp 4 phương pháp truy vấn trên dữ liệu hiện tại trong cơ sở dữ liệu theo tỷ lệ thỏa mãn ràng buộc khách quan (giá, diện tích, phòng, khu vực, tiện ích) và độ trễ.
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2 shrink-0">
            <Select value={selectedMode} onValueChange={(value) => setSelectedMode(value as RequestMode)}>
              <SelectTrigger className="w-[240px] bg-background" aria-label="Chọn phương pháp đánh giá">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">{MODE_LABELS.all}</SelectItem>
                <SelectItem value="fts">{MODE_LABELS.fts}</SelectItem>
                <SelectItem value="vector">{MODE_LABELS.vector}</SelectItem>
                <SelectItem value="hybrid">{MODE_LABELS.hybrid}</SelectItem>
                <SelectItem value="hybrid_graph">{MODE_LABELS.hybrid_graph}</SelectItem>
              </SelectContent>
            </Select>

            {user ? (
              <Button onClick={handleRun} disabled={isStarting || payload.is_running}>
                {payload.is_running || isStarting ? (
                  <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
                ) : (
                  <Play className="h-4 w-4" aria-hidden />
                )}
                {payload.is_running ? "Đang chạy..." : "Chạy đánh giá"}
              </Button>
            ) : (
              <Button asChild variant="outline">
                <Link href="/dang-nhap?next=/du-lieu">Đăng nhập để chạy</Link>
              </Button>
            )}
          </div>
        </div>

        {activeRun && (
          <div className="mt-4 rounded-lg border border-primary/20 bg-accent/60 px-3.5 py-2.5 text-sm text-foreground">
            <span className="inline-flex items-center gap-2 font-medium">
              <Loader2 className="h-4 w-4 animate-spin text-primary" aria-hidden />
              Đang chạy #{activeRun.id} ({MODE_LABELS[activeRun.retrieval_mode] ?? activeRun.retrieval_mode}):{" "}
              {formatNumber(activeRun.queries_done)} / {formatNumber(activeRun.queries_total)} lượt truy vấn
            </span>
          </div>
        )}

        {notice && (
          <p className={`mt-3 text-sm ${notice.isError ? "text-destructive" : "text-muted-foreground"}`}>
            {notice.text}
          </p>
        )}
      </section>

      <section className="rounded-xl border border-border bg-card p-4 sm:p-5">
        <h3 className="text-sm font-semibold text-foreground">So sánh 4 phương pháp (Ablation Study)</h3>
        <p className="mt-0.5 mb-4 text-xs text-muted-foreground">
          Lấy kết quả mới nhất của từng chế độ trên tập 150 câu (top-10).
        </p>

        {comparedModes.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            Chưa có kết quả đánh giá nào. Hãy đăng nhập và bấm <strong>Chạy đánh giá</strong> để tạo bảng so sánh đầu tiên.
          </p>
        ) : (
          <DataTable
            table={{
              columns: [
                "Phương pháp",
                "Khớp toàn bộ @5",
                "Khớp toàn bộ @10",
                "0 kết quả",
                "Latency p50",
                "Latency p95",
                "Cập nhật",
              ],
              numericColumns: [1, 2, 3, 4, 5],
              rows: comparedModes.map((mode) => {
                const item = payload.latest_by_mode[mode]
                return [
                  MODE_LABELS[mode] ?? mode,
                  formatOptionalShare(item.exact_constraint_match_at_5),
                  formatOptionalShare(item.exact_constraint_match_at_10),
                  formatOptionalShare(item.zero_result_rate),
                  formatMs(item.latency_ms?.p50),
                  formatMs(item.latency_ms?.p95),
                  item.finished_at ? formatMoment(item.finished_at) : "–",
                ]
              }),
            }}
          />
        )}
      </section>

      {primaryMode && (
        <div className="grid gap-4 lg:grid-cols-2">
          <section className="rounded-xl border border-border bg-card p-4 sm:p-5">
            <h3 className="text-sm font-semibold text-foreground">
              Tỷ lệ thỏa từng loại ràng buộc @10 ({MODE_LABELS[primaryMode.retrieval_mode] ?? primaryMode.retrieval_mode})
            </h3>
            <p className="mt-0.5 mb-3 text-xs text-muted-foreground">
              So sánh khả năng đáp ứng riêng lẻ từng tiêu chí trong câu truy vấn.
            </p>
            <DataTable
              table={{
                columns: ["Ràng buộc", "Tỷ lệ đúng @10"],
                numericColumns: [1],
                rows: Object.entries(primaryMode.per_constraint_match_at_10).map(([key, value]) => [
                  CONSTRAINT_LABELS[key] ?? key,
                  formatOptionalShare(value),
                ]),
              }}
            />
          </section>

          <section className="rounded-xl border border-border bg-card p-4 sm:p-5">
            <h3 className="text-sm font-semibold text-foreground">
              Khớp toàn bộ @10 theo 7 nhóm truy vấn
            </h3>
            <p className="mt-0.5 mb-3 text-xs text-muted-foreground">
              Phân tách kết quả theo từng nhóm kịch bản trong bộ 150 câu.
            </p>
            <DataTable
              table={{
                columns: ["Nhóm truy vấn", "Khớp toàn bộ @10"],
                numericColumns: [1],
                rows: Object.entries(primaryMode.exact_constraint_match_at_10_by_category).map(([key, value]) => [
                  CATEGORY_LABELS[key] ?? key,
                  formatOptionalShare(value),
                ]),
              }}
            />
          </section>
        </div>
      )}

      <section className="rounded-xl border border-border bg-card p-4 sm:p-5">
        <h3 className="text-sm font-semibold text-foreground">Lịch sử các lần đánh giá</h3>
        <p className="mt-0.5 mb-3 text-xs text-muted-foreground">
          Tối đa {RUN_LIMIT} lần chạy gần nhất.
        </p>

        {payload.runs.length === 0 ? (
          <p className="text-sm text-muted-foreground">Chưa có lịch sử chạy nào.</p>
        ) : (
          <DataTable
            table={{
              columns: ["#", "Thời điểm", "Người chạy", "Chế độ", "Trạng thái", "Tiến độ", "Thời lượng"],
              numericColumns: [0, 5, 6],
              rows: payload.runs.map((run) => {
                const statusBadge = run.status === "completed"
                  ? <StatusLabel level="good">Hoàn tất</StatusLabel>
                  : run.status === "failed"
                    ? <StatusLabel level="serious">Lỗi</StatusLabel>
                    : (
                      <span className="inline-flex items-center gap-1 text-xs font-medium text-foreground">
                        <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-hidden />
                        Đang chạy
                      </span>
                    )
                const duration = run.summary?.duration_seconds
                return [
                  `#${run.id}`,
                  formatMoment(run.started_at),
                  run.requested_by_name || "–",
                  MODE_LABELS[run.retrieval_mode] ?? run.retrieval_mode,
                  statusBadge,
                  `${formatNumber(run.queries_done)}/${formatNumber(run.queries_total)}`,
                  typeof duration === "number" ? `${duration.toFixed(1)}s` : "–",
                ]
              }),
            }}
          />
        )}
      </section>
    </div>
  )
}
