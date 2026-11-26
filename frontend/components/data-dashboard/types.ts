// Shape of GET /api/monitoring (backend src/services/monitoring/service.py), the data dashboard's only input.

/** One bin of a histogram. */
export interface HistogramBin {
  label: string
  count: number
}

/** One bin compared between the training data (reference) and the catalog (current), as shares. */
export interface DriftBin {
  label: string
  reference: number
  current: number
}

/** Drift of one feature: PSI and its reading. */
export interface DriftFeature {
  feature: string
  kind: "numeric" | "categorical"
  psi: number
  status: "stable" | "moderate" | "major"
  bins: DriftBin[]
}

/** Quality numbers of one catalog field. */
export interface FieldQuality {
  field: string
  missing: number
  invalid: number
  distinct?: number
  min?: number | null
  median?: number | null
  max?: number | null
}

/** A listing whose price per m² is far from others of its type. */
export interface SuspiciousListing {
  id: number
  listing_id: number
  title: string | null
  property_type: string | null
  price: number
  area: number
  price_per_m2: number
  score: number
}

/** Tukey box plot numbers of one property type's price per m². */
export interface BoxStatistics {
  label: string
  count: number
  low: number
  q1: number
  median: number
  q3: number
  high: number
  outliers: number
}

/** Test-set metrics of the served model for one segment. */
export interface SegmentMetrics {
  segment: string
  count: number
  r2: number
  rmse_log: number
  mae: number
  median_ae: number
  mape: number
  interval_coverage: number | null
}

/** Overall test metrics of one candidate model. */
export interface BenchmarkRow {
  model: string
  r2: number
  rmse_log: number
  mae: number
  median_ae: number
  mape: number
}

/** Verdict counts of the price model over catalog listings of one group. */
export interface VerdictGroup {
  group: "in_training" | "not_in_training"
  total: number
  below: number
  within: number
  above: number
  refused: number
}

export interface MonitoringSnapshot {
  generated_at: string
  kpis: {
    listings: number
    seed_version: string | null
    loaded_at: string | null
    embedded: number
    graph_listings: number | null
    listings_with_photos: number
    photo_keys: number
    stored_photos: number | null
    model: { name: string; r2: number; mape: number } | null
    in_training: number
  }
  quality: {
    rows: number
    fields: FieldQuality[]
    duplicate_listings: number
    suspicious_prices: { count: number; threshold: number; listings: SuspiciousListing[] }
    posted_by_month: Array<{ month: string; count: number }>
  }
  distributions: {
    property_types: HistogramBin[]
    wards: HistogramBin[]
    other_wards: number
    ward_count: number
    price: HistogramBin[]
    area: HistogramBin[]
    price_per_m2: BoxStatistics[]
  }
  drift: { reference_rows: number; current_rows: number; features: DriftFeature[] } | null
  model: {
    name: string
    trained_report_at: string
    composition: {
      by_source: Record<string, number>
      step_rows: number[]
      train_rows: number
      test_rows: number
      train_by_source: Record<string, number>
    }
    benchmark: BenchmarkRow[]
    segments: SegmentMetrics[]
    error_histogram: Array<{ label: string; [segment: string]: string | number }>
    scatter: Array<[number, number, string]>
  } | null
  predictions: {
    scored: number
    verdicts: VerdictGroup[]
    refused_reasons: Array<{ code: string; count: number }>
    difference_histogram: Array<{ label: string; in_training: number; not_in_training: number }>
  } | null
}
