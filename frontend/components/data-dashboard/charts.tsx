"use client"

// Chart building blocks of the data dashboard, following the dataviz rules: thin marks (bars
// at most 24px, 4px rounded data ends), hairline grid, a legend for two or more series,
// values in the tooltip and in a table view for every chart, text in text colors only.
// Colors are CSS variables (--viz-* in globals.css) so the palette lives in one place.

import { useEffect, useRef, useState, type ReactNode } from "react"
import { AlertTriangle, CircleCheck, OctagonAlert } from "lucide-react"
import {
  Bar,
  BarChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  type TooltipContentProps,
} from "recharts"
import { cn } from "@/lib/utils"
import { niceTicks } from "@/lib/chart-ticks"
import { formatNumber } from "./format"

/** Palette roles; values in globals.css. */
export const VIZ = {
  series1: "var(--viz-series-1)",
  series2: "var(--viz-series-2)",
  series3: "var(--viz-series-3)",
  below: "var(--viz-below)",
  within: "var(--viz-within)",
  above: "var(--viz-above)",
  grid: "var(--viz-grid)",
  axis: "var(--viz-axis)",
  track: "var(--viz-track)",
  ink: "var(--muted-foreground)",
}

const AXIS_TICK = { fontSize: 12, fill: VIZ.ink }

let labelMeasurer: CanvasRenderingContext2D | null | undefined

/**
 * Shorten a label with an ellipsis so it fits a width, measured in the page font at 12px.
 * @param label - full label.
 * @param maxWidth - available width in px.
 * @returns the label, or its longest prefix that fits followed by "…".
 */
function fitLabel(label: string, maxWidth: number): string {
  if (typeof document === "undefined") return label
  if (labelMeasurer === undefined) {
    labelMeasurer = document.createElement("canvas").getContext("2d")
    if (labelMeasurer) labelMeasurer.font = `12px ${getComputedStyle(document.body).fontFamily}`
  }
  if (!labelMeasurer || labelMeasurer.measureText(label).width <= maxWidth) return label
  let end = label.length
  while (end > 1 && labelMeasurer.measureText(`${label.slice(0, end)}…`).width > maxWidth) end -= 1
  return `${label.slice(0, end).trimEnd()}…`
}

/** Recharts tick props used by CategoryTick. */
interface CategoryTickProps {
  x?: number | string
  y?: number | string
  payload?: { value?: unknown }
  maxWidth: number
}

/**
 * A category label on one line, right-aligned against a vertical axis and shortened to fit.
 * Recharts' own tick overestimates accented Vietnamese letters and wraps long labels early.
 * @param props - text position, the category in payload.value, and the width it may use.
 * @returns the label element; a shortened label carries its full text as a tooltip.
 */
function CategoryTick({ x = 0, y = 0, payload, maxWidth }: CategoryTickProps) {
  const label = String(payload?.value ?? "")
  const shown = fitLabel(label, maxWidth)
  return (
    <text x={Number(x)} y={Number(y)} dy={4} textAnchor="end" fontSize={12} fill={VIZ.ink}>
      {shown !== label && <title>{label}</title>}
      {shown}
    </text>
  )
}

/**
 * Width of the category axis of a horizontal bar chart: the wanted width, but at most half of
 * the chart so the bars stay readable on a phone.
 * @param labelWidth - wanted width in px.
 * @param chartWidth - measured chart width in px (0 before the first measurement).
 * @returns the axis width in px.
 */
function categoryAxisWidth(labelWidth: number, chartWidth: number): number {
  return chartWidth > 0 ? Math.min(labelWidth, Math.round(chartWidth * 0.5)) : labelWidth
}

/** Gap between a category label and the axis line: recharts' tick size plus tick margin. */
const CATEGORY_TICK_GAP = 10

/**
 * Largest value across the given keys of the rows.
 * @param rows - chart rows.
 * @param keys - numeric keys to scan.
 * @returns the maximum, or 0 for no rows.
 */
function largestValue(rows: Array<Record<string, unknown>>, keys: string[]): number {
  return rows.reduce((largest, row) => Math.max(largest, ...keys.map((key) => Number(row[key]) || 0)), 0)
}

/**
 * Width of an element in px, kept up to date as it resizes.
 * @returns a ref to attach and the current width (0 before the first measurement).
 */
function useElementWidth<ElementType extends HTMLElement>() {
  const ref = useRef<ElementType>(null)
  const [width, setWidth] = useState(0)
  useEffect(() => {
    const element = ref.current
    if (!element) return
    const observer = new ResizeObserver((entries) => setWidth(Math.round(entries[0].contentRect.width)))
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  return { ref, width }
}

/** The table twin of a chart: headers and rows; numeric columns are right-aligned. */
export interface TableView {
  columns: string[]
  rows: Array<Array<ReactNode>>
  numericColumns?: number[]
}

/** One series of a multi-series chart. */
export interface SeriesSpec {
  key: string
  name: string
  color: string
}

/**
 * Card holding one chart, its title and a toggle to the same numbers as a table.
 * @param props.title - chart title (names the single series when there is one).
 * @param props.note - optional short line under the title.
 * @param props.table - the chart's numbers as a table.
 * @param props.children - the chart.
 * @param props.className - extra classes for the card.
 * @returns the card element.
 */
export function ChartCard({ title, note, table, children, className }: {
  title: string
  note?: ReactNode
  table: TableView
  children: ReactNode
  className?: string
}) {
  const [isTableShown, setIsTableShown] = useState(false)
  return (
    <section className={cn("min-w-0 rounded-xl border border-border bg-card p-4 sm:p-5", className)}>
      <div className="mb-3 flex items-start justify-between gap-3">
        <div className="min-w-0">
          <h3 className="text-sm font-semibold text-foreground">{title}</h3>
          {note && <p className="mt-0.5 text-xs text-muted-foreground">{note}</p>}
        </div>
        <button
          type="button"
          aria-pressed={isTableShown}
          onClick={() => setIsTableShown((current) => !current)}
          className="shrink-0 rounded-md border border-border px-2 py-1 text-xs text-muted-foreground hover:text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring"
        >
          {isTableShown ? "Biểu đồ" : "Bảng"}
        </button>
      </div>
      {isTableShown ? <DataTable table={table} /> : children}
    </section>
  )
}

/**
 * A plain table: the accessible twin of every chart.
 * @param props.table - headers and rows.
 * @returns the table element, scrollable sideways on narrow screens.
 */
export function DataTable({ table }: { table: TableView }) {
  const isNumeric = (index: number) => table.numericColumns?.includes(index)
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-xs">
        <thead>
          <tr className="border-b border-border text-muted-foreground">
            {table.columns.map((column, index) => (
              <th key={column} scope="col" className={cn("px-2 py-1.5 font-medium", isNumeric(index) ? "text-right" : "text-left")}>
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="border-b border-border/60 last:border-0">
              {row.map((cell, index) => (
                <td key={index} className={cn("px-2 py-1.5 text-foreground", isNumeric(index) && "text-right tabular-nums")}>
                  {cell}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/**
 * Legend for two or more series: a swatch shaped like the mark, then the name in text ink.
 * @param props.series - series in drawing order.
 * @param props.mark - "bar" (square swatch) or "dot".
 * @returns the legend row.
 */
export function Legend({ series, mark = "bar" }: { series: SeriesSpec[]; mark?: "bar" | "dot" }) {
  return (
    <ul className="mb-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
      {series.map((item) => (
        <li key={item.key} className="flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className={cn("inline-block h-2.5 w-2.5", mark === "dot" ? "rounded-full" : "rounded-[2px]")}
            style={{ background: item.color }}
          />
          {item.name}
        </li>
      ))}
    </ul>
  )
}

/**
 * Tooltip body: values lead (strong), series names follow; each row keyed by a short line.
 * @param props - recharts tooltip props plus a value formatter.
 * @returns the tooltip box, or null when nothing is hovered.
 */
function ChartTooltip({ active, payload, label, formatValue }: TooltipContentProps & {
  formatValue: (value: number) => string
}) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-md border border-border bg-card px-2.5 py-1.5 text-xs shadow-sm">
      {label !== undefined && <div className="mb-1 text-muted-foreground">{label}</div>}
      {payload.map((item) => (
        <div key={String(item.dataKey ?? item.name)} className="flex items-center gap-2">
          <span aria-hidden="true" className="inline-block h-0.5 w-3" style={{ background: item.color ?? item.fill }} />
          <span className="font-semibold text-foreground tabular-nums">{formatValue(Number(item.value))}</span>
          {payload.length > 1 && <span className="text-muted-foreground">{item.name}</span>}
        </div>
      ))}
    </div>
  )
}

/**
 * Ranked horizontal bars of one series, largest first, value at each bar's tip.
 * @param props.data - rows of label and value, already ranked.
 * @param props.formatValue - formats a value for labels and tooltip.
 * @param props.labelWidth - width reserved for category labels, in px.
 * @param props.ariaLabel - accessible name of the chart.
 * @returns the chart.
 */
export function RankedBars({ data, formatValue = (value) => formatNumber(value), labelWidth = 150, ariaLabel }: {
  data: Array<{ label: string; value: number }>
  formatValue?: (value: number) => string
  labelWidth?: number
  ariaLabel: string
}) {
  const ticks = niceTicks(largestValue(data, ["value"]))
  const { ref, width } = useElementWidth<HTMLDivElement>()
  const axisWidth = categoryAxisWidth(labelWidth, width)
  return (
    <div ref={ref} role="img" aria-label={ariaLabel}>
      <ResponsiveContainer width="100%" height={Math.max(120, data.length * 30 + 24)}>
        <BarChart data={data} layout="vertical" margin={{ top: 4, right: 56, bottom: 4, left: 0 }} barCategoryGap={8}>
          <CartesianGrid horizontal={false} stroke={VIZ.grid} />
          <XAxis type="number" tick={AXIS_TICK} stroke={VIZ.axis} domain={[0, ticks[ticks.length - 1]]} ticks={ticks}
            tickFormatter={(value) => formatNumber(value, 2)} />
          <YAxis type="category" dataKey="label" width={axisWidth} stroke={VIZ.axis} interval={0}
            tick={(tickProps: Omit<CategoryTickProps, "maxWidth">) => <CategoryTick {...tickProps} maxWidth={axisWidth - CATEGORY_TICK_GAP} />} />
          <Tooltip cursor={{ fill: VIZ.track, opacity: 0.4 }} content={(props) => <ChartTooltip {...props} formatValue={formatValue} />} />
          <Bar
            dataKey="value"
            fill={VIZ.series1}
            radius={[0, 4, 4, 0]}
            maxBarSize={18}
            isAnimationActive={false}
            label={{ position: "right", fill: VIZ.ink, fontSize: 12, formatter: (value) => formatValue(Number(value)) }}
          />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

/**
 * Columns of one series over ordered bins (a histogram).
 * @param props.data - rows of label and value, in bin order.
 * @param props.formatValue - formats a value for the tooltip and axis.
 * @param props.ariaLabel - accessible name of the chart.
 * @returns the chart.
 */
export function Columns({ data, formatValue = (value) => formatNumber(value), ariaLabel }: {
  data: Array<{ label: string; value: number }>
  formatValue?: (value: number) => string
  ariaLabel: string
}) {
  const ticks = niceTicks(largestValue(data, ["value"]))
  return (
    <div role="img" aria-label={ariaLabel}>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={data} margin={{ top: 8, right: 8, bottom: 4, left: 0 }} barCategoryGap={2}>
          <CartesianGrid vertical={false} stroke={VIZ.grid} />
          <XAxis dataKey="label" tick={AXIS_TICK} stroke={VIZ.axis} interval={0} angle={data.length > 8 ? -30 : 0}
            textAnchor={data.length > 8 ? "end" : "middle"} height={data.length > 8 ? 48 : 24} />
          <YAxis tick={AXIS_TICK} stroke={VIZ.axis} width={44} domain={[0, ticks[ticks.length - 1]]} ticks={ticks}
            tickFormatter={(value) => formatValue(Number(value))} />
          <Tooltip cursor={{ fill: VIZ.track, opacity: 0.4 }} content={(props) => <ChartTooltip {...props} formatValue={formatValue} />} />
          <Bar dataKey="value" fill={VIZ.series1} radius={[4, 4, 0, 0]} maxBarSize={24} isAnimationActive={false} />
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

/**
 * Two series side by side per category, with a legend.
 * @param props.data - one row per category: label plus one value per series key.
 * @param props.series - the two series.
 * @param props.formatValue - formats values (shares as percentages, counts as numbers).
 * @param props.layout - "columns" (vertical bars, ordered bins) or "bars" (horizontal, long names).
 * @param props.labelWidth - width of category labels for horizontal bars, in px.
 * @param props.height - chart height in px; horizontal bars size themselves when omitted.
 * @param props.ariaLabel - accessible name of the chart.
 * @returns the legend and chart.
 */
export function GroupedBars({ data, series, formatValue, layout = "columns", labelWidth = 140, height, ariaLabel }: {
  data: Array<Record<string, string | number>>
  series: SeriesSpec[]
  formatValue: (value: number) => string
  layout?: "columns" | "bars"
  labelWidth?: number
  height?: number
  ariaLabel: string
}) {
  const isHorizontal = layout === "bars"
  const chartHeight = height ?? (isHorizontal ? Math.max(140, data.length * 36 + 24) : 220)
  const isCrowded = !isHorizontal && data.length > 8
  const ticks = niceTicks(largestValue(data, series.map((item) => item.key)))
  const valueDomain: [number, number] = [0, ticks[ticks.length - 1]]
  const { ref, width } = useElementWidth<HTMLDivElement>()
  const axisWidth = categoryAxisWidth(labelWidth, width)
  return (
    <div ref={ref} role="img" aria-label={ariaLabel}>
      <Legend series={series} />
      <ResponsiveContainer width="100%" height={chartHeight}>
        <BarChart data={data} layout={isHorizontal ? "vertical" : "horizontal"} barGap={2} barCategoryGap={isHorizontal ? 6 : 8}
          margin={{ top: 4, right: 12, bottom: 4, left: 0 }}>
          <CartesianGrid horizontal={isHorizontal ? false : true} vertical={isHorizontal} stroke={VIZ.grid} />
          {isHorizontal ? (
            <>
              <XAxis type="number" tick={AXIS_TICK} stroke={VIZ.axis} domain={valueDomain} ticks={ticks}
                tickFormatter={(value) => formatValue(Number(value))} />
              <YAxis type="category" dataKey="label" width={axisWidth} stroke={VIZ.axis} interval={0}
            tick={(tickProps: Omit<CategoryTickProps, "maxWidth">) => <CategoryTick {...tickProps} maxWidth={axisWidth - CATEGORY_TICK_GAP} />} />
            </>
          ) : (
            <>
              <XAxis dataKey="label" tick={AXIS_TICK} stroke={VIZ.axis} interval={0} angle={isCrowded ? -30 : 0}
                textAnchor={isCrowded ? "end" : "middle"} height={isCrowded ? 52 : 24} />
              <YAxis tick={AXIS_TICK} stroke={VIZ.axis} width={48} domain={valueDomain} ticks={ticks}
                tickFormatter={(value) => formatValue(Number(value))} />
            </>
          )}
          <Tooltip cursor={{ fill: VIZ.track, opacity: 0.4 }} content={(props) => <ChartTooltip {...props} formatValue={formatValue} />} />
          {series.map((item) => (
            <Bar key={item.key} dataKey={item.key} name={item.name} fill={item.color}
              radius={isHorizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]} maxBarSize={isHorizontal ? 12 : 16}
              isAnimationActive={false} />
          ))}
        </BarChart>
      </ResponsiveContainer>
    </div>
  )
}

/** One scatter point: actual and predicted price in billion VND. */
interface PricePoint {
  actual: number
  predicted: number
}

/**
 * Draws a scatter point: an 8px dot with a surface ring, inside a transparent 24px hit area.
 * @param props - recharts shape props (cx, cy, fill).
 * @returns the point group.
 */
function ScatterPoint(props: { cx?: number; cy?: number; fill?: string }) {
  const { cx = 0, cy = 0, fill } = props
  return (
    <g>
      <circle cx={cx} cy={cy} r={12} fill="transparent" />
      <circle cx={cx} cy={cy} r={4} fill={fill} stroke="var(--card)" strokeWidth={1.5} />
    </g>
  )
}

/**
 * Predicted against actual price on log axes, one series per segment, with the y = x line.
 * @param props.series - each segment's points with its name and color.
 * @param props.domain - shared axis range in billion VND.
 * @param props.ariaLabel - accessible name of the chart.
 * @returns the legend and chart.
 */
export function PriceScatter({ series, domain, ariaLabel }: {
  series: Array<SeriesSpec & { points: PricePoint[] }>
  domain: [number, number]
  ariaLabel: string
}) {
  const ticks = [1, 2, 5, 10, 20, 50].filter((tick) => tick >= domain[0] && tick <= domain[1])
  const formatPrice = (value: number) => `${formatNumber(value, 1)} tỷ`
  return (
    <div role="img" aria-label={ariaLabel}>
      <Legend series={series} mark="dot" />
      <ResponsiveContainer width="100%" height={300}>
        <ScatterChart margin={{ top: 8, right: 12, bottom: 24, left: 4 }}>
          <CartesianGrid stroke={VIZ.grid} />
          <XAxis type="number" dataKey="actual" name="Giá rao" scale="log" domain={domain} ticks={ticks}
            tick={AXIS_TICK} stroke={VIZ.axis} allowDataOverflow
            label={{ value: "Giá rao (tỷ, thang log)", position: "insideBottom", offset: -14, fontSize: 12, fill: VIZ.ink }} />
          <YAxis type="number" dataKey="predicted" name="Giá dự đoán" scale="log" domain={domain} ticks={ticks}
            tick={AXIS_TICK} stroke={VIZ.axis} width={44} allowDataOverflow
            label={{ value: "Dự đoán", angle: -90, position: "insideLeft", fontSize: 12, fill: VIZ.ink }} />
          <ReferenceLine segment={[{ x: domain[0], y: domain[0] }, { x: domain[1], y: domain[1] }]} stroke={VIZ.axis} />
          <Tooltip
            cursor={false}
            content={({ active, payload }) => {
              const point = active ? (payload?.[0]?.payload as PricePoint | undefined) : undefined
              if (!point) return null
              const error = (point.predicted / point.actual - 1) * 100
              return (
                <div className="rounded-md border border-border bg-card px-2.5 py-1.5 text-xs shadow-sm">
                  <div className="font-semibold text-foreground tabular-nums">
                    {formatPrice(point.predicted)} <span className="font-normal text-muted-foreground">dự đoán</span>
                  </div>
                  <div className="text-muted-foreground tabular-nums">{formatPrice(point.actual)} giá thật</div>
                  <div className="text-muted-foreground tabular-nums">sai số {error > 0 ? "+" : ""}{formatNumber(error, 1)}%</div>
                </div>
              )
            }}
          />
          {series.map((item) => (
            <Scatter key={item.key} name={item.name} data={item.points} fill={item.color} shape={<ScatterPoint />}
              isAnimationActive={false} />
          ))}
        </ScatterChart>
      </ResponsiveContainer>
    </div>
  )
}

/** Box plot numbers of one row. */
export interface BoxRow {
  label: string
  low: number
  q1: number
  median: number
  q3: number
  high: number
  count: number
  outliers: number
}

/**
 * Horizontal Tukey box plots on one shared axis: whiskers, the quartile box and the median.
 * Drawn at the container's measured width so text stays 12px; under 480px the labels move
 * above their boxes. Each row names its numbers on hover (native tooltip) and in the table view.
 * @param props.rows - one row per category.
 * @param props.unit - unit shown after values.
 * @param props.ariaLabel - accessible name of the chart.
 * @returns the chart as inline SVG inside a measuring wrapper.
 */
export function BoxPlot({ rows, unit, ariaLabel }: { rows: BoxRow[]; unit: string; ariaLabel: string }) {
  const { ref, width } = useElementWidth<HTMLDivElement>()
  const isNarrow = width < 480
  const labelWidth = isNarrow ? 0 : 200
  const labelHeight = isNarrow ? 18 : 0
  const rowHeight = 34 + labelHeight
  const axisHeight = 26
  const height = rows.length * rowHeight + axisHeight
  const ticks = niceTicks(Math.max(...rows.map((row) => row.high)), 5)
  const maximum = ticks[ticks.length - 1]
  const plotLeft = labelWidth + 4
  const plotWidth = Math.max(0, width - plotLeft - 16)
  const x = (value: number) => plotLeft + (value / maximum) * plotWidth
  return (
    <div ref={ref} style={{ height }}>
      {width > 0 && (
        <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img" aria-label={ariaLabel}>
          {ticks.map((tick) => (
            <g key={tick}>
              <line x1={x(tick)} x2={x(tick)} y1={0} y2={rows.length * rowHeight} stroke={VIZ.grid} />
              <text x={x(tick)} y={rows.length * rowHeight + 16} textAnchor="middle" fontSize={12} fill={VIZ.ink}>
                {formatNumber(tick)}
              </text>
            </g>
          ))}
          {rows.map((row, index) => {
            const middle = index * rowHeight + labelHeight + (rowHeight - labelHeight) / 2
            return (
              <g key={row.label}>
                <title>{`${row.label}: trung vị ${formatNumber(row.median)} ${unit}, 50% ở giữa ${formatNumber(row.q1)}–${formatNumber(row.q3)} ${unit}, ${formatNumber(row.count)} tin, ${formatNumber(row.outliers)} tin ngoại lai`}</title>
                {isNarrow ? (
                  <text x={plotLeft} y={index * rowHeight + 13} fontSize={12} fill={VIZ.ink}>{row.label}</text>
                ) : (
                  <text x={labelWidth - 8} y={middle + 4} textAnchor="end" fontSize={12} fill={VIZ.ink}>{row.label}</text>
                )}
                <line x1={x(row.low)} x2={x(row.high)} y1={middle} y2={middle} stroke={VIZ.axis} strokeWidth={1.5} />
                <line x1={x(row.low)} x2={x(row.low)} y1={middle - 5} y2={middle + 5} stroke={VIZ.axis} strokeWidth={1.5} />
                <line x1={x(row.high)} x2={x(row.high)} y1={middle - 5} y2={middle + 5} stroke={VIZ.axis} strokeWidth={1.5} />
                <rect x={x(row.q1)} y={middle - 9} width={Math.max(2, x(row.q3) - x(row.q1))} height={18} rx={4}
                  fill={VIZ.track} stroke={VIZ.series1} strokeWidth={1.5} />
                <line x1={x(row.median)} x2={x(row.median)} y1={middle - 9} y2={middle + 9} stroke={VIZ.series1} strokeWidth={2.5} />
              </g>
            )
          })}
        </svg>
      )}
    </div>
  )
}

/** One part of a segmented bar. */
export interface Segment {
  key: string
  name: string
  value: number
  color: string
  /** Text color inside the block: white on saturated fills, near-black on light ones. */
  ink: "light" | "dark"
}

/**
 * A tall segmented bar: one whole split into parts, each share printed inside when it fits.
 * @param props.segments - parts in drawing order.
 * @param props.label - accessible name of the bar.
 * @returns the bar.
 */
export function SegmentedBar({ segments, label }: { segments: Segment[]; label: string }) {
  const total = segments.reduce((sum, segment) => sum + segment.value, 0)
  return (
    <div role="img" aria-label={label} className="flex h-7 w-full gap-0.5">
      {segments.filter((segment) => segment.value > 0).map((segment) => {
        const share = total ? segment.value / total : 0
        return (
          <div
            key={segment.key}
            title={`${segment.name}: ${formatNumber(segment.value)} (${formatNumber(share * 100, 1)}%)`}
            className={cn("flex min-w-0 items-center rounded-[4px] px-1.5 text-xs font-medium",
              segment.ink === "light" ? "text-white" : "text-neutral-900")}
            style={{ width: `${share * 100}%`, background: segment.color }}
          >
            {share >= 0.08 && <span className="truncate">{formatNumber(share * 100, 0)}%</span>}
          </div>
        )
      })}
    </div>
  )
}

/**
 * Meter list: one ratio per named row, bars aligned, the figure at the end.
 * @param props.rows - name, ratio (0–1), figure text and an optional secondary note.
 * @returns the list.
 */
export function MeterList({ rows }: { rows: Array<{ name: string; ratio: number; figure: string; note?: string }> }) {
  return (
    // One grid shared by all rows (subgrid), so every track ends where the widest figure starts.
    <ul className="grid grid-cols-[7rem_1fr_auto] gap-x-3 gap-y-2 text-xs">
      {rows.map((row) => (
        <li key={row.name} className="col-span-3 grid grid-cols-subgrid items-center">
          <span className="text-foreground">{row.name}</span>
          <span className="h-2 overflow-hidden rounded-full" style={{ background: VIZ.track }} aria-hidden="true">
            <span className="block h-full rounded-full" style={{ width: `${Math.min(100, row.ratio * 100)}%`, background: VIZ.series1 }} />
          </span>
          <span className="text-right tabular-nums text-muted-foreground">
            {row.figure}
            {row.note && <span className="ml-1.5 text-foreground">{row.note}</span>}
          </span>
        </li>
      ))}
    </ul>
  )
}

/** Status of a measurement, shown with an icon and a label, never color alone. */
export type StatusLevel = "good" | "warning" | "serious"

const STATUS_ICONS = { good: CircleCheck, warning: AlertTriangle, serious: OctagonAlert }
const STATUS_COLORS = { good: "var(--viz-status-good)", warning: "var(--viz-status-warning)", serious: "var(--viz-status-serious)" }

/**
 * Status label: colored icon plus text in the text color.
 * @param props.level - good, warning or serious.
 * @param props.children - the label.
 * @returns the inline status element.
 */
export function StatusLabel({ level, children }: { level: StatusLevel; children: ReactNode }) {
  const Icon = STATUS_ICONS[level]
  return (
    <span className="inline-flex items-center gap-1 text-xs font-medium text-foreground">
      <Icon aria-hidden="true" className="h-3.5 w-3.5" style={{ color: STATUS_COLORS[level] }} />
      {children}
    </span>
  )
}

/**
 * Stat tile: a label, a large value and an optional meta line or status.
 * @param props.label - sentence-case label, no colon.
 * @param props.value - the number, already formatted.
 * @param props.meta - optional detail under the value.
 * @param props.status - optional status shown under the meta line.
 * @returns the tile.
 */
export function StatTile({ label, value, meta, status }: { label: string; value: string; meta?: ReactNode; status?: ReactNode }) {
  return (
    <div className="rounded-xl border border-border bg-card p-4">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-2xl font-semibold text-foreground">{value}</div>
      {meta && <div className="mt-1 text-xs text-muted-foreground">{meta}</div>}
      {status && <div className="mt-2">{status}</div>}
    </div>
  )
}
