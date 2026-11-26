"use client"

// The data dashboard (/du-lieu), in two tabs. "Dữ liệu và model": catalog health, data quality,
// drift between the training data and the catalog, the price model's test quality and its
// verdicts over the catalog, all fed by one request to GET /api/monitoring. "Lịch sử huấn luyện":
// the price model's training runs and the "Train ngay" button (training-history.tsx).

import { useEffect, useState, type ReactNode } from "react"
import Link from "next/link"
import { Tabs } from "@/components/tabs"
import { Skeleton } from "@/components/ui/skeleton"
import { fetchJson, HttpError, isAbortError, type ApiEnvelope } from "@/lib/http"
import {
  BoxPlot,
  ChartCard,
  Columns,
  DataTable,
  GroupedBars,
  Legend,
  MeterList,
  PriceScatter,
  RankedBars,
  SegmentedBar,
  StatTile,
  StatusLabel,
  VIZ,
  type Segment,
  type StatusLevel,
} from "./charts"
import {
  DRIFT_STATUS_LABELS,
  FEATURE_LABELS,
  FIELD_LABELS,
  INVALID_RULES,
  REFUSAL_LABELS,
  SEGMENT_LABELS,
  SOURCE_LABELS,
  formatMoment,
  formatNumber,
  formatShare,
} from "./format"
import { TrainingHistory } from "./training-history"
import type { MonitoringSnapshot, VerdictGroup } from "./types"

const LOAD_ERROR = "Không tải được số liệu. Vui lòng thử lại sau."

/** Suspicious listings shown before "Xem thêm". */
const SUSPICIOUS_PREVIEW = 10

/** Interval coverage below this share (percent) is flagged: the band shown to users is too narrow. */
const COVERAGE_TARGET = 80

/**
 * Section heading with an optional one-line description.
 * @param props.title - section title.
 * @param props.description - optional line under the title.
 * @param props.children - the section body.
 * @returns the section.
 */
function DashboardSection({ title, description, children }: { title: string; description?: ReactNode; children: ReactNode }) {
  return (
    <section className="space-y-3">
      <div>
        <h2 className="text-lg font-semibold text-foreground">{title}</h2>
        {description && <p className="mt-0.5 text-sm text-muted-foreground text-pretty">{description}</p>}
      </div>
      {children}
    </section>
  )
}

/**
 * A card without a table toggle, for content that already is a table or text.
 * @param props.title - card title.
 * @param props.note - optional line under the title.
 * @param props.children - card body.
 * @param props.className - extra classes.
 * @returns the card.
 */
function Panel({ title, note, children, className }: { title: string; note?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`min-w-0 rounded-xl border border-border bg-card p-4 sm:p-5 ${className ?? ""}`}>
      <h3 className="text-sm font-semibold text-foreground">{title}</h3>
      {note && <p className="mt-0.5 mb-3 text-xs text-muted-foreground">{note}</p>}
      {children}
    </section>
  )
}

/**
 * The dashboard's two tabs; a newly deployed model makes the data tab reload its numbers.
 * @returns the tabs.
 */
export function DataDashboard() {
  const [reloadCount, setReloadCount] = useState(0)
  return (
    <Tabs label="Nội dung trang Dữ liệu" items={[
      { key: "data", label: "Dữ liệu và model", panel: <DataOverview reloadCount={reloadCount} /> },
      { key: "training", label: "Lịch sử huấn luyện",
        panel: <TrainingHistory onModelChanged={() => setReloadCount((count) => count + 1)} /> },
    ]} />
  )
}

/**
 * Fetches the snapshot; shows a skeleton while loading and a retry on failure.
 * @param props.reloadCount - changes when the numbers must be fetched again (a new model is served).
 * @returns the data tab.
 */
function DataOverview({ reloadCount }: { reloadCount: number }) {
  const [snapshot, setSnapshot] = useState<MonitoringSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    const controller = new AbortController()
    setError(null)
    fetchJson<ApiEnvelope<MonitoringSnapshot>>("/api/monitoring", { signal: controller.signal }, LOAD_ERROR)
      .then((body) => {
        if (body?.data) setSnapshot(body.data)
        else setError(LOAD_ERROR)
      })
      .catch((reason: unknown) => {
        if (isAbortError(reason)) return
        setError(reason instanceof HttpError ? reason.message : LOAD_ERROR)
      })
    return () => controller.abort()
  }, [attempt, reloadCount])

  if (error && !snapshot) {
    return (
      <div className="rounded-xl border border-border bg-card p-6 text-sm">
        <p className="text-destructive">{error}</p>
        <button type="button" onClick={() => setAttempt((current) => current + 1)}
          className="mt-3 rounded-md border border-border px-3 py-1.5 text-foreground focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
          Thử lại
        </button>
      </div>
    )
  }
  if (!snapshot) {
    return (
      <div className="space-y-4" aria-busy="true" aria-label="Đang tải số liệu">
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
          {Array.from({ length: 6 }, (_, index) => <Skeleton key={index} className="h-24 rounded-xl" />)}
        </div>
        <Skeleton className="h-72 rounded-xl" />
        <Skeleton className="h-72 rounded-xl" />
      </div>
    )
  }
  return (
    <div className="space-y-10">
      <p className="text-sm text-muted-foreground">Cập nhật lúc {formatMoment(snapshot.generated_at)}.</p>
      <OverviewSection snapshot={snapshot} />
      <QualitySection snapshot={snapshot} />
      <DistributionSection snapshot={snapshot} />
      {snapshot.drift && <DriftSection drift={snapshot.drift} />}
      {snapshot.model && <ModelSection model={snapshot.model} />}
      {snapshot.predictions && <PredictionSection predictions={snapshot.predictions} catalogSize={snapshot.kpis.listings} />}
    </div>
  )
}

/** Headline tiles: catalog, vectors, graph, photos, model and training overlap. */
function OverviewSection({ snapshot }: { snapshot: MonitoringSnapshot }) {
  const { kpis } = snapshot
  const share = (count: number) => formatShare(kpis.listings ? count / kpis.listings : 0)
  return (
    <DashboardSection title="Tổng quan">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-6">
        <StatTile label="Tin trong catalog" value={formatNumber(kpis.listings)}
          meta={kpis.loaded_at ? `Nạp lúc ${formatMoment(kpis.loaded_at)}` : "Chưa có lần nạp bằng seed"} />
        <StatTile label="Có vector" value={formatNumber(kpis.embedded)} meta={`${share(kpis.embedded)} catalog`} />
        <StatTile label="Node trên đồ thị" value={kpis.graph_listings === null ? "Không kết nối" : formatNumber(kpis.graph_listings)}
          meta="Neo4j" />
        <StatTile label="Ảnh" value={formatNumber(kpis.photo_keys)}
          meta={kpis.stored_photos === null ? `${formatNumber(kpis.listings_with_photos)} tin có ảnh`
            : `${formatNumber(kpis.stored_photos)} ảnh trong kho`} />
        <StatTile label="Model định giá" value={kpis.model ? `R² ${formatNumber(kpis.model.r2, 3)}` : "Chưa có"}
          meta={kpis.model ? `${kpis.model.name} · MAPE ${formatNumber(kpis.model.mape, 1)}%` : undefined} />
        <StatTile label="Tin trùng tập huấn luyện" value={formatNumber(kpis.in_training)} meta={`${share(kpis.in_training)} catalog`}
          status={kpis.in_training > 0 ? <StatusLabel level="warning">Rò rỉ dữ liệu khi định giá</StatusLabel> : undefined} />
      </div>
    </DashboardSection>
  )
}

/** Missing and invalid values per field, suspicious prices, freshness. */
function QualitySection({ snapshot }: { snapshot: MonitoringSnapshot }) {
  const { quality } = snapshot
  const [isEverySuspiciousShown, setIsEverySuspiciousShown] = useState(false)
  const fields = [...quality.fields].sort((first, second) => second.missing - first.missing || second.invalid - first.invalid)
  const suspicious = quality.suspicious_prices
  return (
    <DashboardSection title="Chất lượng dữ liệu"
      description={`${formatNumber(quality.rows)} tin; ${formatNumber(quality.duplicate_listings)} mã tin bị trùng.`}>
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="Giá trị thiếu theo trường" note="Thanh: tỷ lệ tin thiếu trường đó; số sai theo quy tắc ghi bên cạnh."
          table={{
            columns: ["Trường", "Thiếu", "Tỷ lệ thiếu", "Sai", "Quy tắc sai", "Số giá trị khác nhau"],
            numericColumns: [1, 2, 3, 5],
            rows: fields.map((field) => [FIELD_LABELS[field.field] ?? field.field, formatNumber(field.missing),
              formatShare(field.missing / quality.rows), formatNumber(field.invalid), INVALID_RULES[field.field] ?? "—",
              field.distinct === undefined ? "—" : formatNumber(field.distinct)]),
          }}>
          <MeterList rows={fields.map((field) => ({
            name: FIELD_LABELS[field.field] ?? field.field,
            ratio: field.missing / quality.rows,
            figure: `${formatNumber(field.missing)} · ${formatShare(field.missing / quality.rows)}`,
            note: field.invalid ? `${formatNumber(field.invalid)} sai` : undefined,
          }))} />
        </ChartCard>
        <ChartCard title="Tin đăng theo tháng"
          table={{ columns: ["Tháng", "Số tin"], numericColumns: [1],
            rows: quality.posted_by_month.map((item) => [monthLabel(item.month), formatNumber(item.count)]) }}>
          <Columns ariaLabel="Số tin đăng theo tháng"
            data={quality.posted_by_month.map((item) => ({ label: monthLabel(item.month), value: item.count }))} />
        </ChartCard>
      </div>
      <Panel title="Đơn giá bất thường"
        note={`${formatNumber(suspicious.count)} tin có đơn giá lệch mạnh so với tin cùng loại (modified z-score vượt ${formatNumber(suspicious.threshold, 1)}), xếp từ lệch nhất. Đơn giá quá thấp thường do crawler lấy nhầm con số đầu tiên trong tiêu đề.`}>
        <DataTable table={{
          columns: ["Tin", "Loại", "Giá (tỷ)", "Diện tích (m²)", "Đơn giá (triệu/m²)", "z"],
          numericColumns: [2, 3, 4, 5],
          rows: suspicious.listings.slice(0, isEverySuspiciousShown ? undefined : SUSPICIOUS_PREVIEW).map((item) => [
            <Link key={item.id} href={`/chi-tiet/${item.id}`} className="line-clamp-1 max-w-[22rem] text-primary hover:underline">
              {item.title || `Tin ${item.listing_id}`}
            </Link>,
            item.property_type ?? "—", formatNumber(item.price, 3), formatNumber(item.area, 1),
            formatNumber(item.price_per_m2, 1), formatNumber(item.score, 1),
          ]),
        }} />
        {suspicious.listings.length > SUSPICIOUS_PREVIEW && (
          <button type="button" onClick={() => setIsEverySuspiciousShown((current) => !current)}
            className="mt-2 text-xs text-primary hover:underline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ring">
            {isEverySuspiciousShown ? "Thu gọn" : `Xem ${suspicious.listings.length} tin lệch nhất`}
          </button>
        )}
      </Panel>
    </DashboardSection>
  )
}

/** "2026-09" → "09/2026". */
function monthLabel(month: string): string {
  const [year, number] = month.split("-")
  return `${number}/${year}`
}

/** Catalog composition: types, wards, price and area histograms, price per m² per type. */
function DistributionSection({ snapshot }: { snapshot: MonitoringSnapshot }) {
  const { distributions } = snapshot
  const histogramTable = (unit: string, bins: Array<{ label: string; count: number }>) => ({
    columns: [unit, "Số tin"], numericColumns: [1], rows: bins.map((bin) => [bin.label, formatNumber(bin.count)]),
  })
  return (
    <DashboardSection title="Phân bố catalog">
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="Loại BĐS" table={histogramTable("Loại", distributions.property_types)}>
          <RankedBars ariaLabel="Số tin theo loại bất động sản" labelWidth={210}
            data={distributions.property_types.map((item) => ({ label: item.label, value: item.count }))} />
        </ChartCard>
        <ChartCard title="Đơn giá theo loại (triệu/m²)" note="Hộp: 50% tin ở giữa; vạch đậm: trung vị; râu: tới 1,5 lần khoảng tứ phân vị."
          table={{
            columns: ["Loại", "Số tin", "Thấp", "Q1", "Trung vị", "Q3", "Cao", "Ngoại lai"], numericColumns: [1, 2, 3, 4, 5, 6, 7],
            rows: distributions.price_per_m2.map((row) => [row.label, formatNumber(row.count), formatNumber(row.low),
              formatNumber(row.q1), formatNumber(row.median), formatNumber(row.q3), formatNumber(row.high), formatNumber(row.outliers)]),
          }}>
          <BoxPlot rows={distributions.price_per_m2} unit="triệu/m²" ariaLabel="Đơn giá theo loại bất động sản" />
        </ChartCard>
        <ChartCard title="Giá rao (tỷ)" table={histogramTable("Khoảng giá (tỷ)", distributions.price)}>
          <Columns ariaLabel="Phân bố giá rao" data={distributions.price.map((bin) => ({ label: bin.label, value: bin.count }))} />
        </ChartCard>
        <ChartCard title="Diện tích (m²)" table={histogramTable("Khoảng diện tích (m²)", distributions.area)}>
          <Columns ariaLabel="Phân bố diện tích" data={distributions.area.map((bin) => ({ label: bin.label, value: bin.count }))} />
        </ChartCard>
        <ChartCard title={`${distributions.wards.length} phường nhiều tin nhất`} className="lg:col-span-2"
          note={`Còn ${formatNumber(distributions.other_wards)} tin ở ${formatNumber(distributions.ward_count - distributions.wards.length)} phường khác.`}
          table={histogramTable("Phường", distributions.wards)}>
          <RankedBars ariaLabel="Số tin theo phường" labelWidth={200}
            data={distributions.wards.map((item) => ({ label: item.label, value: item.count }))} />
        </ChartCard>
      </div>
    </DashboardSection>
  )
}

const DRIFT_LEVELS: Record<string, StatusLevel> = { stable: "good", moderate: "warning", major: "serious" }

/** Training data against the catalog, one small chart per feature. */
function DriftSection({ drift }: { drift: NonNullable<MonitoringSnapshot["drift"]> }) {
  const series = [
    { key: "reference", name: `Tập huấn luyện (${formatNumber(drift.reference_rows)} tin)`, color: VIZ.series1 },
    { key: "current", name: `Catalog (${formatNumber(drift.current_rows)} tin)`, color: VIZ.series2 },
  ]
  return (
    <DashboardSection title="Drift dữ liệu"
      description="Phân bố của tập huấn luyện so với catalog đang hiển thị, đo bằng PSI: dưới 0,1 là ổn định, 0,1–0,25 là lệch vừa, trên 0,25 là lệch mạnh.">
      <div className="grid gap-4 lg:grid-cols-2">
        {drift.features.map((feature, index) => (
          <ChartCard key={feature.feature} title={FEATURE_LABELS[feature.feature] ?? feature.feature}
            className={drift.features.length % 2 === 1 && index === drift.features.length - 1 ? "lg:col-span-2" : undefined}
            note={<StatusLabel level={DRIFT_LEVELS[feature.status]}>
              {DRIFT_STATUS_LABELS[feature.status]} · PSI {formatNumber(feature.psi, 3)}</StatusLabel>}
            table={{
              columns: ["Khoảng", "Tập huấn luyện", "Catalog"], numericColumns: [1, 2],
              rows: feature.bins.map((bin) => [bin.label, formatShare(bin.reference), formatShare(bin.current)]),
            }}>
            <GroupedBars ariaLabel={`Phân bố ${FEATURE_LABELS[feature.feature] ?? feature.feature}`} series={series}
              data={feature.bins.map((bin) => ({ label: bin.label, reference: bin.reference, current: bin.current }))}
              formatValue={(value) => formatShare(value, 0)} layout={feature.kind === "categorical" ? "bars" : "columns"}
              labelWidth={feature.feature === "ward" ? 200 : 160} />
          </ChartCard>
        ))}
      </div>
    </DashboardSection>
  )
}

/** Test-set quality of the served price model, its training data and its errors. */
function ModelSection({ model }: { model: NonNullable<MonitoringSnapshot["model"]> }) {
  const sourceOrder = ["new", "both", "old"]
  const sourceColors: Record<string, string> = { new: VIZ.series1, both: VIZ.series3, old: VIZ.series2 }
  const sources: Segment[] = sourceOrder.filter((key) => model.composition.train_by_source[key]).map((key) => ({
    key, name: SOURCE_LABELS[key] ?? key, value: model.composition.train_by_source[key], color: sourceColors[key], ink: "light",
  }))
  const [raw, cleaned, withoutOutliers] = model.composition.step_rows
  const segmentSeries = [
    { key: "Căn hộ", name: "Căn hộ", color: VIZ.series1 },
    { key: "Nhà đất", name: "Nhà đất", color: VIZ.series2 },
  ]
  const prices = model.scatter.flatMap(([actual, predicted]) => [actual, predicted]).filter((value) => value > 0)
  const domain: [number, number] = [Math.max(0.3, Math.min(...prices) * 0.9), Math.max(...prices) * 1.1]
  return (
    <DashboardSection title="Model định giá"
      description={`${model.name}, đánh giá trên ${formatNumber(model.composition.test_rows)} tin kiểm tra không dùng để huấn luyện.`}>
      <Panel title="Hiệu năng trên tập kiểm tra"
        note="Khoảng hiển thị trên trang chi tiết là ±12% với căn hộ và ±20% với nhà đất; cột cuối cho biết bao nhiêu giá thật thực sự nằm trong khoảng đó.">
        <DataTable table={{
          columns: ["Phân khúc", "Số tin", "R²", "MAE (tỷ)", "MAPE", "Giá thật nằm trong khoảng"],
          numericColumns: [1, 2, 3, 4, 5],
          rows: model.segments.map((row) => [
            SEGMENT_LABELS[row.segment] ?? row.segment, formatNumber(row.count), formatNumber(row.r2, 3), formatNumber(row.mae, 2),
            `${formatNumber(row.mape, 1)}%`,
            row.interval_coverage === null ? "—" : row.interval_coverage < COVERAGE_TARGET
              ? <StatusLabel level="warning">{formatNumber(row.interval_coverage, 1)}%</StatusLabel>
              : `${formatNumber(row.interval_coverage, 1)}%`,
          ]),
        }} />
      </Panel>
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="Nguồn của tập huấn luyện"
          note={`${formatNumber(raw)} tin trong data/v3 → ${formatNumber(cleaned)} sau làm sạch → ${formatNumber(withoutOutliers)} sau bỏ ngoại lai → ${formatNumber(model.composition.train_rows)} huấn luyện + ${formatNumber(model.composition.test_rows)} kiểm tra.`}
          table={{ columns: ["Nguồn", "Số tin huấn luyện", "Tỷ lệ"], numericColumns: [1, 2],
            rows: sources.map((source) => [source.name, formatNumber(source.value), formatShare(source.value / model.composition.train_rows)]) }}>
          <Legend series={sources.map((source) => ({ key: source.key, name: `${source.name} (${formatNumber(source.value)})`, color: source.color }))} />
          <SegmentedBar label="Nguồn của tập huấn luyện" segments={sources} />
        </ChartCard>
        <ChartCard title="So sánh model ứng viên"
          table={{ columns: ["Model", "R²", "MAPE"], numericColumns: [1, 2],
            rows: model.benchmark.map((row) => [row.model, formatNumber(row.r2, 3), `${formatNumber(row.mape, 1)}%`]) }}>
          <RankedBars ariaLabel="R² của từng model trên tập kiểm tra" labelWidth={150} formatValue={(value) => formatNumber(value, 3)}
            data={[...model.benchmark].sort((first, second) => second.r2 - first.r2).map((row) => ({ label: row.model, value: row.r2 }))} />
        </ChartCard>
        <ChartCard title="Giá dự đoán so với giá thật" note="Mỗi chấm là một tin kiểm tra; càng gần đường chéo càng đúng."
          table={{
            columns: ["Phân khúc", "Giá thật (tỷ)", "Dự đoán (tỷ)", "Sai số"], numericColumns: [1, 2, 3],
            rows: [...model.scatter].sort((first, second) => Math.abs(second[1] / second[0] - 1) - Math.abs(first[1] / first[0] - 1))
              .map(([actual, predicted, segment]) => [segment, formatNumber(actual, 2), formatNumber(predicted, 2),
                `${formatNumber((predicted / actual - 1) * 100, 1)}%`]),
          }}>
          <PriceScatter ariaLabel="Giá dự đoán so với giá thật trên tập kiểm tra" domain={domain}
            series={segmentSeries.map((item) => ({
              ...item,
              points: model.scatter.filter(([, , segment]) => segment === item.key).map(([actual, predicted]) => ({ actual, predicted })),
            }))} />
        </ChartCard>
        <ChartCard title="Sai số dự đoán (%)" note="(dự đoán − giá thật) / giá thật, trên tập kiểm tra."
          table={{ columns: ["Sai số (%)", "Căn hộ", "Nhà đất"], numericColumns: [1, 2],
            rows: model.error_histogram.map((row) => [row.label, formatNumber(Number(row["Căn hộ"] ?? 0)), formatNumber(Number(row["Nhà đất"] ?? 0))]) }}>
          <GroupedBars ariaLabel="Phân bố sai số dự đoán" series={segmentSeries} data={model.error_histogram}
            formatValue={(value) => formatNumber(value)} />
        </ChartCard>
      </div>
    </DashboardSection>
  )
}

const VERDICT_GROUP_LABELS: Record<VerdictGroup["group"], string> = {
  in_training: "Tin model đã học",
  not_in_training: "Tin model chưa học",
}

/** The model's verdicts over the catalog, split by training membership. */
function PredictionSection({ predictions, catalogSize }: {
  predictions: NonNullable<MonitoringSnapshot["predictions"]>
  catalogSize: number
}) {
  const verdictSegments = (group: VerdictGroup): Segment[] => [
    { key: "below", name: "Thấp hơn khoảng", value: group.below, color: VIZ.below, ink: "light" },
    { key: "within", name: "Trong khoảng", value: group.within, color: VIZ.within, ink: "dark" },
    { key: "above", name: "Cao hơn khoảng", value: group.above, color: VIZ.above, ink: "light" },
  ]
  const groupTotals = Object.fromEntries(predictions.verdicts.map((group) => [group.group, group.below + group.within + group.above]))
  const differences = predictions.difference_histogram.map((row) => ({
    label: row.label,
    in_training: groupTotals.in_training ? row.in_training / groupTotals.in_training : 0,
    not_in_training: groupTotals.not_in_training ? row.not_in_training / groupTotals.not_in_training : 0,
  }))
  const groupSeries = [
    { key: "in_training", name: VERDICT_GROUP_LABELS.in_training, color: VIZ.series1 },
    { key: "not_in_training", name: VERDICT_GROUP_LABELS.not_in_training, color: VIZ.series2 },
  ]
  return (
    <DashboardSection title="Giám sát định giá trên catalog"
      description={`Model định giá ${formatNumber(predictions.scored)}/${formatNumber(catalogSize)} tin. Tin model đã học thường được ước tính sát giá rao, nên tỷ lệ "trong khoảng" ở nhóm đó cao hơn thực tế.`}>
      <ChartCard title="Kết luận định giá theo nhóm tin" note="Tỷ lệ trên các tin được định giá; tin bị từ chối đếm riêng."
        table={{
          columns: ["Nhóm", "Tổng", "Thấp hơn", "Trong khoảng", "Cao hơn", "Không định giá", "Tỷ lệ trong khoảng"],
          numericColumns: [1, 2, 3, 4, 5, 6],
          rows: predictions.verdicts.map((group) => [VERDICT_GROUP_LABELS[group.group], formatNumber(group.total),
            formatNumber(group.below), formatNumber(group.within), formatNumber(group.above), formatNumber(group.refused),
            formatShare(groupTotals[group.group] ? group.within / groupTotals[group.group] : 0)]),
        }}>
        <Legend series={verdictSegments(predictions.verdicts[0]).map((segment) => ({ key: segment.key, name: segment.name, color: segment.color }))} />
        <div className="space-y-3">
          {predictions.verdicts.map((group) => (
            <div key={group.group} className="grid gap-1.5 sm:grid-cols-[11rem_1fr_9rem] sm:items-center">
              <span className="text-xs text-foreground">{VERDICT_GROUP_LABELS[group.group]} ({formatNumber(group.total)})</span>
              <SegmentedBar label={`Kết luận định giá: ${VERDICT_GROUP_LABELS[group.group]}`} segments={verdictSegments(group)} />
              <span className="text-xs text-muted-foreground sm:text-right">{formatNumber(group.refused)} tin không định giá</span>
            </div>
          ))}
        </div>
      </ChartCard>
      <div className="grid gap-4 lg:grid-cols-2">
        <ChartCard title="Chênh lệch giá rao so với ước tính (%)" note="Tỷ lệ trong từng nhóm; nhóm đã học dồn quanh 0%."
          table={{ columns: ["Chênh lệch (%)", VERDICT_GROUP_LABELS.in_training, VERDICT_GROUP_LABELS.not_in_training], numericColumns: [1, 2],
            rows: differences.map((row) => [row.label, formatShare(row.in_training), formatShare(row.not_in_training)]) }}>
          <GroupedBars ariaLabel="Chênh lệch giá rao so với giá ước tính" series={groupSeries} data={differences}
            formatValue={(value) => formatShare(value, 0)} />
        </ChartCard>
        <ChartCard title="Lý do không định giá"
          table={{ columns: ["Lý do", "Số tin"], numericColumns: [1],
            rows: predictions.refused_reasons.map((item) => [REFUSAL_LABELS[item.code] ?? item.code, formatNumber(item.count)]) }}>
          <RankedBars ariaLabel="Số tin bị từ chối định giá theo lý do" labelWidth={280}
            data={predictions.refused_reasons.map((item) => ({ label: REFUSAL_LABELS[item.code] ?? item.code, value: item.count }))} />
        </ChartCard>
      </div>
    </DashboardSection>
  )
}
