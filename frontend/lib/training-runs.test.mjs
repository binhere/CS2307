// Tests for the wording of the training history: each outcome says what happened and why, with
// the numbers that decided it.
// Run with: npm test (node --test, TypeScript sources loaded with --experimental-strip-types).
import assert from "node:assert/strict"
import { test } from "node:test"

const { formatDuration, runOutcome, runTrigger } = await import("./training-runs.ts")

const run = (status, reason = null, summary = {}) => ({
  id: 1, trigger: "manual", requested_by_name: "Lan", status, reason, started_at: "2026-11-26T03:00:00+00:00", finished_at: null, summary,
})
const metrics = (r2) => ({ r2, rmse_log: 0.2, mae: 0.5, median_ae: 0.3, mape: 15 })

test("each outcome says why, with the numbers that decided it", () => {
  assert.equal(runOutcome(run("deployed", null, { model_name: "HistGradientBoosting" }), 0.1).label, "Đã dùng")
  assert.equal(runOutcome(run("rejected", "not_better", { challenger: metrics(0.801), champion: metrics(0.83) }), 0.1).detail,
    "R² 0,801 không cao hơn 0,830 của model đang dùng.")
  assert.equal(runOutcome(run("skipped", "not_enough_new_data", { new_rows: 120, pool_rows: 2596 }), 0.1).detail,
    "Chỉ có 120/2.596 tin mới (4,6%), cần ít nhất 10%.")
  assert.equal(runOutcome(run("failed", "interrupted"), 0.1).detail, "Bị ngắt giữa chừng.")
  assert.equal(runOutcome(run("failed", "error", { error: "MemoryError" }), 0.1).detail, "Lỗi khi train (MemoryError).")
  assert.equal(runOutcome(run("running"), 0.1).level, "neutral")
})

test("the trigger names the user or the schedule, and durations read naturally", () => {
  assert.equal(runTrigger(run("deployed")), "Lan")
  assert.equal(runTrigger({ ...run("skipped"), trigger: "schedule", requested_by_name: null }), "Lịch hằng ngày")
  assert.equal(formatDuration(42.4), "42\u00a0giây")
  assert.equal(formatDuration(125), "2\u00a0phút 5\u00a0giây")
  assert.equal(formatDuration(undefined), "–")
})
