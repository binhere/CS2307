// Tests for the dashboard's axis ticks: every axis starts at 0 and steps by 1, 2 or 5 × a power
// of ten, so readers see 10%, 20%, 30% rather than the 19% or 28% recharts produced on its own.
// Run with: npm test (node --test, TypeScript sources loaded with --experimental-strip-types).
import assert from "node:assert/strict"
import { test } from "node:test"

const { niceStep, niceTicks } = await import("./chart-ticks.ts")

test("a raw step rounds up to 1, 2 or 5 times a power of ten", () => {
  assert.equal(niceStep(0.09), 0.1)
  assert.equal(niceStep(0.208), 0.5)
  assert.equal(niceStep(429), 500)
  assert.equal(niceStep(150), 200)
})

test("a share axis gets round percentages and ends just above the largest bar", () => {
  assert.deepEqual(niceTicks(0.36), [0, 0.1, 0.2, 0.3, 0.4])
})

test("a maximum that is already round is the last tick, without an extra step", () => {
  assert.deepEqual(niceTicks(0.2), [0, 0.05, 0.1, 0.15, 0.2])
  assert.deepEqual(niceTicks(0.3), [0, 0.1, 0.2, 0.3])
})

test("counts and scores get round steps too", () => {
  assert.deepEqual(niceTicks(1716), [0, 500, 1000, 1500, 2000])
  assert.deepEqual(niceTicks(0.833), [0, 0.5, 1])
})

test("an empty axis still has two ticks", () => {
  assert.deepEqual(niceTicks(0), [0, 1])
  assert.deepEqual(niceTicks(Number.NaN), [0, 1])
})
