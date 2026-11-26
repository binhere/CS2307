"use client"

// The "Lịch sử huấn luyện" tab of the data dashboard: every training run of the price model,
// newest first, with what decided its outcome, and the "Train ngay" button for signed-in users.
// The server runs one training at a time: pressing the button meanwhile says the server is busy.
// While a run is going the list refreshes every few seconds; when a run deploys a model, the
// dashboard's other tab is told to reload its numbers.

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { Loader2, MinusCircle, Play } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { authHeaders, useAuth } from "@/lib/auth-context"
import { HttpError, extractError, fetchJson, isAbortError, type ApiEnvelope } from "@/lib/http"
import { formatDuration, formatFixed, runOutcome, runTrigger, type TrainingHistory as History, type TrainingRun } from "@/lib/training-runs"
import { DataTable, StatusLabel } from "./charts"
import { formatMoment, formatNumber } from "./format"

const LOAD_ERROR = "Không tải được lịch sử huấn luyện. Vui lòng thử lại sau."
const REFRESH_WHILE_RUNNING_MS = 5000
const RUN_LIMIT = 30

/**
 * A run's outcome: status icon and label, then the reason in one muted line.
 * @param props.run - the run.
 * @param props.newDataShare - the schedule's new-data threshold.
 * @returns the cell content.
 */
function RunStatus({ run, newDataShare }: { run: TrainingRun; newDataShare: number }) {
  const outcome = runOutcome(run, newDataShare)
  return (
    <div className="min-w-48 space-y-0.5">
      {outcome.level === "neutral" ? (
        <span className="inline-flex items-center gap-1 text-xs font-medium text-foreground">
          {run.status === "running"
            ? <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground motion-reduce:animate-none" aria-hidden />
            : <MinusCircle className="h-3.5 w-3.5 text-muted-foreground" aria-hidden />}
          {outcome.label}
        </span>
      ) : <StatusLabel level={outcome.level}>{outcome.label}</StatusLabel>}
      {outcome.detail && <p className="text-xs text-muted-foreground">{outcome.detail}</p>}
    </div>
  )
}

/**
 * The new model's metric next to the served model's, e.g. "0,842 / 0,830".
 * @param run - the run.
 * @param pick - reads the metric from a model's metrics.
 * @param format - formats the metric.
 * @returns the pair, or "–" when the run did not train.
 */
function metricPair(run: TrainingRun, pick: (metrics: { r2: number; mape: number }) => number, format: (value: number) => string): string {
  const { challenger, champion } = run.summary ?? {}
  if (!challenger) return "–"
  return champion ? `${format(pick(challenger))} / ${format(pick(champion))}` : format(pick(challenger))
}

/**
 * The training history tab.
 * @param props.onModelChanged - called when a run newly deployed a model, so other views reload.
 * @returns the tab content.
 */
export function TrainingHistory({ onModelChanged }: { onModelChanged?: () => void }) {
  const { user } = useAuth()
  const [history, setHistory] = useState<History | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<{ text: string; isError: boolean } | null>(null)
  const [isStarting, setIsStarting] = useState(false)
  const [refreshCount, setRefreshCount] = useState(0)
  // The newest deployed run seen so far; a newer one means the served model changed.
  const deployedSeen = useRef<number | null>(null)
  const onModelChangedRef = useRef(onModelChanged)
  onModelChangedRef.current = onModelChanged

  useEffect(() => {
    const controller = new AbortController()
    fetchJson<ApiEnvelope<History>>(`/api/models/price/runs?limit=${RUN_LIMIT}`, { signal: controller.signal }, LOAD_ERROR)
      .then((body) => {
        if (!body?.data) {
          setError(LOAD_ERROR)
          return
        }
        setError(null)
        setHistory(body.data)
        const newestDeployed = body.data.runs.find((run) => run.status === "deployed")?.id ?? null
        if (deployedSeen.current !== null && newestDeployed !== null && newestDeployed > deployedSeen.current) onModelChangedRef.current?.()
        deployedSeen.current = newestDeployed ?? deployedSeen.current ?? 0
      })
      .catch((reason: unknown) => {
        if (!isAbortError(reason)) setError(reason instanceof HttpError ? reason.message : LOAD_ERROR)
      })
    return () => controller.abort()
  }, [refreshCount])

  useEffect(() => {
    if (!history?.is_running) return
    const timer = setTimeout(() => setRefreshCount((count) => count + 1), REFRESH_WHILE_RUNNING_MS)
    return () => clearTimeout(timer)
  }, [history])

  /** Ask the server to train now; a busy server answers with its message. */
  const startTraining = async () => {
    setIsStarting(true)
    setNotice(null)
    try {
      await fetchJson<ApiEnvelope<{ run_id: number }>>("/api/models/price/train", { method: "POST", headers: authHeaders() },
        "Chưa bắt đầu train được.")
      setNotice({ text: "Đã bắt đầu train. Kết quả sẽ hiện trong bảng bên dưới.", isError: false })
    } catch (startError) {
      const text = startError instanceof HttpError
        ? startError.status === 401 ? "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại." : extractError(startError.payload, startError.message)
        : "Chưa bắt đầu train được."
      setNotice({ text, isError: true })
    } finally {
      setIsStarting(false)
      setRefreshCount((count) => count + 1)
    }
  }

  if (!history) {
    return error ? (
      <div className="rounded-xl border border-border bg-card p-6 text-sm">
        <p className="text-destructive">{error}</p>
        <button type="button" onClick={() => setRefreshCount((count) => count + 1)}
          className="mt-3 rounded-md border border-border px-3 py-1.5 text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
          Thử lại
        </button>
      </div>
    ) : <Skeleton className="h-72 rounded-xl" aria-label="Đang tải lịch sử huấn luyện" />
  }

  const rows = history.runs.map((run) => {
    const summary = run.summary ?? {}
    return [
      formatMoment(run.started_at),
      runTrigger(run),
      <RunStatus key="status" run={run} newDataShare={history.new_data_share} />,
      metricPair(run, (metrics) => metrics.r2, (value) => formatFixed(value, 3)),
      metricPair(run, (metrics) => metrics.mape, (value) => `${formatFixed(value, 1)}%`),
      summary.training_rows !== undefined ? formatNumber(summary.training_rows) : summary.pool_rows !== undefined ? formatNumber(summary.pool_rows) : "–",
      summary.imputed_bedrooms !== undefined
        // Non-breaking spaces: the cell may wrap only at the dot, never inside "tọa độ: 106".
        ? `phòng\u00a0ngủ:\u00a0${formatNumber(summary.imputed_bedrooms)} · tọa\u00a0độ:\u00a0${formatNumber(summary.imputed_coordinates ?? 0)}` : "–",
      formatDuration(summary.duration_seconds),
      summary.peak_memory_mb !== undefined ? `${formatNumber(summary.peak_memory_mb)}\u00a0MB` : "–",
    ]
  })

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <p className="max-w-3xl text-sm text-muted-foreground text-pretty">
          Model định giá được train lại lúc {String(history.schedule_hour).padStart(2, "0")}:00 mỗi ngày khi có ít nhất{" "}
          {formatNumber(history.new_data_share * 100)}% tin mới. Model mới chỉ được dùng khi chính xác hơn model đang dùng trên cùng một tập
          kiểm tra.
        </p>
        {user ? (
          <Button type="button" onClick={() => void startTraining()} disabled={isStarting}>
            {isStarting ? <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden /> : <Play className="h-4 w-4" aria-hidden />}
            Train ngay
          </Button>
        ) : (
          <Button asChild variant="outline"><Link href="/dang-nhap?next=/du-lieu">Đăng nhập để train</Link></Button>
        )}
      </div>
      {notice && (
        <p role={notice.isError ? "alert" : "status"}
          className={notice.isError ? "rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive" : "rounded-lg bg-muted px-3 py-2 text-sm text-foreground"}>
          {notice.text}
        </p>
      )}
      <section className="min-w-0 rounded-xl border border-border bg-card p-4 sm:p-5" aria-label="Các lần huấn luyện">
        {rows.length === 0 ? <p className="text-sm text-muted-foreground">Chưa có lần huấn luyện nào.</p> : (
          <DataTable table={{
            columns: ["Bắt đầu", "Nguồn kích hoạt", "Kết quả", "R² (mới / đang dùng)", "MAPE (mới / đang dùng)", "Số tin huấn luyện", "Điền giá trị thiếu",
              "Thời gian chạy", "RAM dùng tối đa"],
            numericColumns: [3, 4, 5, 8],
            rows,
          }} />
        )}
      </section>
    </div>
  )
}
