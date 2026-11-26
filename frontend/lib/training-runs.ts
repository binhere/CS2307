// The price model's training history as the "Lịch sử huấn luyện" tab shows it: the shape the
// backend sends (GET /api/models/price/runs) and the Vietnamese wording of each run's outcome.

/** Metrics of a model on the holdout set: R² and RMSE on log price, errors in billion VND, MAPE in percent. */
export interface TrainingMetrics {
  r2: number
  rmse_log: number
  mae: number
  median_ae: number
  mape: number
}

/** What a run counted and measured; fields appear as far as the run got. */
export interface TrainingRunSummary {
  input_rows?: number
  pool_rows?: number
  new_rows?: number
  holdout_rows?: number
  training_rows?: number
  imputed_bedrooms?: number
  imputed_coordinates?: number
  model_name?: string
  challenger?: TrainingMetrics
  champion?: TrainingMetrics
  champion_name?: string
  duration_seconds?: number
  peak_memory_mb?: number
  error?: string
}

export type TrainingRunStatus = "running" | "deployed" | "rejected" | "skipped" | "failed"

/** One training run. */
export interface TrainingRun {
  id: number
  trigger: "schedule" | "manual"
  requested_by_name: string | null
  status: TrainingRunStatus
  reason: string | null
  started_at: string
  finished_at: string | null
  summary: TrainingRunSummary | null
}

/** The history and the schedule's rules. */
export interface TrainingHistory {
  runs: TrainingRun[]
  /** Hour (Vietnam time) of the daily run. */
  schedule_hour: number
  /** Share of new rows the daily run needs before it trains, e.g. 0.1. */
  new_data_share: number
  is_running: boolean
}

/** How a run's outcome is shown: a status level for the icon, a short label and one line of detail. */
export interface RunOutcome {
  level: "good" | "warning" | "serious" | "neutral"
  label: string
  detail: string | null
}

/**
 * Format a number with Vietnamese separators and exactly `digits` decimals, so values compared
 * side by side line up ("0,830 / 0,838", never "0,83 / 0,838").
 * @param value - the number.
 * @param digits - decimals to show.
 * @returns e.g. "0,842" or "2.596".
 */
export function formatFixed(value: number, digits = 0): string {
  return value.toLocaleString("vi-VN", { minimumFractionDigits: digits, maximumFractionDigits: digits })
}

/**
 * The outcome of a run in words.
 * @param run - the run.
 * @param newDataShare - the schedule's threshold, e.g. 0.1, quoted when a run was skipped.
 * @returns its level, label and detail line.
 */
export function runOutcome(run: TrainingRun, newDataShare: number): RunOutcome {
  const summary = run.summary ?? {}
  switch (run.status) {
    case "running":
      return { level: "neutral", label: "Đang train", detail: null }
    case "deployed":
      return { level: "good", label: "Đã dùng", detail: summary.model_name ? `Model ${summary.model_name} đã thay thế model trước.` : null }
    case "rejected":
      return {
        level: "warning", label: "Không dùng",
        detail: summary.challenger && summary.champion
          ? `R² ${formatFixed(summary.challenger.r2, 3)} không cao hơn ${formatFixed(summary.champion.r2, 3)} của model đang dùng.`
          : "Model mới không tốt hơn model đang dùng.",
      }
    case "skipped": {
      const share = summary.pool_rows ? (summary.new_rows ?? 0) / summary.pool_rows : 0
      return {
        level: "neutral", label: "Bỏ qua",
        detail: `Chỉ có ${formatFixed(summary.new_rows ?? 0)}/${formatFixed(summary.pool_rows ?? 0)} tin mới `
          + `(${formatFixed(share * 100, 1)}%), cần ít nhất ${formatFixed(newDataShare * 100)}%.`,
      }
    }
    case "failed":
      if (run.reason === "interrupted") return { level: "serious", label: "Lỗi", detail: "Bị ngắt giữa chừng." }
      if (run.reason === "too_few_rows") return { level: "serious", label: "Lỗi", detail: "Quá ít tin để train." }
      return { level: "serious", label: "Lỗi", detail: summary.error ? `Lỗi khi train (${summary.error}).` : "Lỗi khi train." }
  }
}

/**
 * Who or what started a run.
 * @param run - the run.
 * @returns "Lịch hằng ngày", or the name of the user who pressed "Train ngay".
 */
export function runTrigger(run: TrainingRun): string {
  return run.trigger === "schedule" ? "Lịch hằng ngày" : run.requested_by_name || "Người dùng"
}

/**
 * Seconds as a short duration.
 * @param seconds - the duration, or undefined for a run that has not finished.
 * @returns e.g. "45 giây", "2 phút 5 giây", or "–" (number and unit joined by a non-breaking space).
 */
export function formatDuration(seconds: number | undefined): string {
  if (seconds === undefined) return "–"
  const whole = Math.round(seconds)
  // Non-breaking spaces keep a number on the line of its unit in narrow table cells.
  if (whole < 60) return `${whole}\u00a0giây`
  return `${Math.floor(whole / 60)}\u00a0phút${whole % 60 ? ` ${whole % 60}\u00a0giây` : ""}`
}
