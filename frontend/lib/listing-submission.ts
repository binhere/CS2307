// The listing form's data: the document sent to POST /v1/listings, the backend's validation report,
// and the form's choices. Required fields have their own inputs; optional fields are typed as one
// JSON object (the schema's "metadata"). The server converts and checks every value, so this only
// assembles the document and reads the JSON.

/** One problem the server found with a field. */
export interface ListingProblem {
  field: string
  label: string
  value: unknown
  code: string
  message: string
  suggestion?: string
}

/** One value or field name the server rewrote. */
export interface ListingConversion {
  field: string
  kind: "value" | "field" | "derived" | "geocoded"
  from: unknown
  to: unknown
  note?: string
}

/** The server's report on one listing (validate_listing). */
export interface ListingReport {
  index: number
  reference: string | null
  is_valid: boolean
  listing: Record<string, unknown>
  errors: ListingProblem[]
  warnings: ListingProblem[]
  conversions: ListingConversion[]
}

/** A category option of the form. */
export interface ListingOption {
  code: string
  label: string
}

/** The form's choices (GET /v1/listings/reference). */
export interface ListingReference {
  wards: string[]
  former_districts: string[]
  /** Official ward -> the former districts its area belonged to before 1 July 2025. */
  ward_former_districts: Record<string, string[]>
  /** Former district -> the unit it counts as ("Quận 2" -> "Thành phố Thủ Đức"). */
  former_district_groups: Record<string, string>
  property_types: ListingOption[]
  legal_statuses: ListingOption[]
  furnishings: ListingOption[]
  limits: { photos: number }
}

/** The required fields as typed in the form. */
export interface RequiredFields {
  title: string
  description: string
  property_type: string
  price_billion_vnd: string
  area_m2: string
  ward: string
  former_district: string
  posted_date: string
}

/** Example of the optional JSON, shown as the textarea's placeholder. */
export const METADATA_EXAMPLE = `{
  "bedroom_count": 2,
  "bathroom_count": 2,
  "legal_status": "so_hong_rieng",
  "furnishing": "day_du",
  "street": "Đường Số 60",
  "project": "Căn hộ DEFINE",
  "latitude": 10.7792,
  "longitude": 106.7571
}`

/**
 * Read the optional fields typed as JSON.
 * @param text - the textarea's content; empty means no optional fields.
 * @returns the object, or a Vietnamese error message when the text is not one JSON object.
 */
export function parseMetadataText(text: string): { metadata: Record<string, unknown> } | { error: string } {
  if (!text.trim()) return { metadata: {} }
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    // The browser's parser message is English ("Unexpected token…"); the Vietnamese sentence is enough.
    return { error: "Thông tin thêm không phải JSON hợp lệ." }
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { error: "Thông tin thêm phải là một JSON object {…}." }
  }
  return { metadata: parsed as Record<string, unknown> }
}

/**
 * Assemble the listing document from the form.
 * @param fields - required fields as typed; blank ones are left out so the server reports them
 *   (or fills them, e.g. the former district from the ward).
 * @param metadataText - optional fields as JSON text.
 * @returns the document, or a Vietnamese error message when the JSON cannot be read.
 */
export function buildListingDocument(
  fields: RequiredFields,
  metadataText: string,
): { document: Record<string, unknown> } | { error: string } {
  const parsed = parseMetadataText(metadataText)
  if ("error" in parsed) return parsed
  const document: Record<string, unknown> = {}
  for (const [name, value] of Object.entries(fields)) {
    if (value.trim()) document[name] = value.trim()
  }
  document.metadata = parsed.metadata
  return { document }
}

/** A location picked on the map. */
export interface PickedLocation {
  latitude: number
  longitude: number
  /** The street at that point, when known. */
  street?: string
}

/**
 * Write a picked location into the optional JSON, keeping everything else the user typed.
 * @param metadataText - the textarea's content (a JSON object or empty).
 * @param location - the picked point and its street; null removes latitude and longitude.
 * @returns the new textarea content, pretty-printed, or null when the text is not a JSON object.
 */
export function withLocation(metadataText: string, location: PickedLocation | null): string | null {
  const parsed = parseMetadataText(metadataText)
  if ("error" in parsed) return null
  const metadata = { ...parsed.metadata }
  if (!location) {
    delete metadata.latitude
    delete metadata.longitude
  } else {
    const round = (value: number) => Math.round(value * 1e7) / 1e7
    metadata.latitude = round(location.latitude)
    metadata.longitude = round(location.longitude)
    if (location.street) metadata.street = location.street
  }
  return Object.keys(metadata).length ? JSON.stringify(metadata, null, 2) : ""
}

/**
 * The coordinates in the optional JSON, to show the pin where the listing already is.
 * @param metadataText - the textarea's content.
 * @returns latitude and longitude when both are numbers, else null.
 */
export function coordinatesFromMetadata(metadataText: string): { latitude: number; longitude: number } | null {
  const parsed = parseMetadataText(metadataText)
  if ("error" in parsed) return null
  const { latitude, longitude } = parsed.metadata
  return typeof latitude === "number" && typeof longitude === "number" && Number.isFinite(latitude) && Number.isFinite(longitude)
    ? { latitude, longitude }
    : null
}

/**
 * Today's date in Vietnam as YYYY-MM-DD, the posted date's default.
 * @param now - the current time.
 * @returns the date string.
 */
export function todayInVietnam(now: Date = new Date()): string {
  return new Date(now.getTime() + 7 * 3600 * 1000).toISOString().slice(0, 10)
}

/**
 * A redirect target from a query string, kept only when it is a path on this site.
 * @param value - the "next" query value.
 * @returns the path, or null for absent, absolute or protocol-relative values ("//evil.example").
 */
export function safeRedirectPath(value: string | null | undefined): string | null {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return null
  return value
}

/** Where a reported field shows on the form: a required field's own input, or the JSON box. */
export type FormArea = keyof RequiredFields | "metadata"

/** The required fields, in form order. */
export const REQUIRED_FIELD_NAMES: readonly (keyof RequiredFields)[] = [
  "title", "description", "property_type", "price_billion_vnd", "area_m2", "ward", "former_district", "posted_date",
]

/** What the server said about one area of the form. */
export interface AreaFeedback {
  errors: ListingProblem[]
  warnings: ListingProblem[]
  conversions: ListingConversion[]
}

/**
 * The form area a reported field belongs to.
 * @param field - the report's field path, e.g. "price_billion_vnd", "metadata.bedroom_count", "latitude".
 * @returns the required field itself, or "metadata" for optional fields, coordinates and the rest.
 */
export function areaOfField(field: string): FormArea {
  return (REQUIRED_FIELD_NAMES as readonly string[]).includes(field) ? (field as keyof RequiredFields) : "metadata"
}

/**
 * Split a report by form area, so each input can show its own errors, warnings and rewritten values.
 * @param report - the server's report, or null before the first check.
 * @returns feedback for every area (empty lists where there is nothing to say).
 */
export function feedbackByArea(report: ListingReport | null): Record<FormArea, AreaFeedback> {
  const areas = [...REQUIRED_FIELD_NAMES, "metadata"] as FormArea[]
  const feedback = Object.fromEntries(
    areas.map((area): [FormArea, AreaFeedback] => [area, { errors: [], warnings: [], conversions: [] }]),
  ) as Record<FormArea, AreaFeedback>
  if (!report) return feedback
  for (const problem of report.errors) feedback[areaOfField(problem.field)].errors.push(problem)
  for (const problem of report.warnings) feedback[areaOfField(problem.field)].warnings.push(problem)
  for (const conversion of report.conversions) feedback[areaOfField(conversion.field)].conversions.push(conversion)
  return feedback
}

/**
 * Units a ward's area came from, with the names that count as the same unit
 * (Thủ Đức city wards also accept "Quận 2", "Quận 9" and "Quận Thủ Đức").
 * @param reference - the form's choices.
 * @param ward - an official ward name.
 * @returns the set of unit names, or null when the ward is not an official name.
 */
function unitsOfWard(reference: ListingReference, ward: string): Set<string> | null {
  const districts = reference.ward_former_districts[ward.trim()]
  if (!districts) return null
  return new Set(districts.map((district) => reference.former_district_groups[district] ?? district))
}

/**
 * The former districts to offer once a ward is chosen.
 * @param reference - the form's choices.
 * @param ward - the ward as typed.
 * @returns the former districts the ward belonged to (and their other names); every former district
 *   while the ward is not an official name yet.
 */
export function formerDistrictOptions(reference: ListingReference, ward: string): string[] {
  const units = unitsOfWard(reference, ward)
  if (!units) return reference.former_districts
  return reference.former_districts.filter((district) => units.has(reference.former_district_groups[district] ?? district))
}

/**
 * The wards to offer once a former district is chosen.
 * @param reference - the form's choices.
 * @param formerDistrict - the former district as typed.
 * @returns the wards made (partly) from it; every ward while it is not an official name yet.
 */
export function wardOptions(reference: ListingReference, formerDistrict: string): string[] {
  const district = formerDistrict.trim()
  if (!(district in reference.former_district_groups)) return reference.wards
  const unit = reference.former_district_groups[district]
  return reference.wards.filter((ward) => unitsOfWard(reference, ward)?.has(unit))
}

/**
 * The former district to keep after the ward changed.
 * @param reference - the form's choices.
 * @param ward - the new ward as typed.
 * @param current - the former district as typed.
 * @returns current when it fits the ward (or the ward is not recognised yet); else the ward's only
 *   former district; else "" so the user picks among the ward's few options.
 */
export function scopedFormerDistrict(reference: ListingReference, ward: string, current: string): string {
  const units = unitsOfWard(reference, ward)
  if (!units) return current
  const currentUnit = reference.former_district_groups[current.trim()]
  if (currentUnit && units.has(currentUnit)) return current
  const districts = reference.ward_former_districts[ward.trim()]
  return districts.length === 1 ? districts[0] : ""
}
