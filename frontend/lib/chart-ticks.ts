// Axis ticks for the data dashboard's charts. Kept free of React so node --test can load it.

/**
 * A round step (1, 2 or 5 × a power of ten) close to a raw step, for axis ticks.
 * @param raw - rough step size.
 * @returns the rounded step.
 */
export function niceStep(raw: number): number {
  const power = 10 ** Math.floor(Math.log10(raw))
  const fraction = raw / power
  return (fraction <= 1 ? 1 : fraction <= 2 ? 2 : fraction <= 5 ? 5 : 10) * power
}

/**
 * Round axis ticks from zero: steps of 1, 2 or 5 × a power of ten, ending at or above the maximum.
 * Recharts 3 splits [0, dataMax] evenly, which gives ticks such as 19% or 28%.
 * @param maximum - largest value on the axis.
 * @param count - rough number of steps wanted.
 * @returns tick values from 0 upwards; [0, 1] when there is nothing to show.
 */
export function niceTicks(maximum: number, count = 4): number[] {
  if (!(maximum > 0)) return [0, 1]
  const step = niceStep(maximum / count)
  const steps = Math.ceil(maximum / step - 1e-9)
  // toPrecision removes float residue such as 0.30000000000000004.
  return Array.from({ length: steps + 1 }, (_, index) => Number((index * step).toPrecision(12)))
}
