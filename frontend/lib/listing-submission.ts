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

/**
 * Put coordinates into the optional JSON, keeping everything else the user typed.
 * @param metadataText - the textarea's content (must be a JSON object or empty).
 * @param latitude - decimal degrees.
 * @param longitude - decimal degrees.
 * @returns the new textarea content, pretty-printed, or null when the text is not a JSON object.
 */
export function withCoordinates(metadataText: string, latitude: number, longitude: number): string | null {
  const parsed = parseMetadataText(metadataText)
  if ("error" in parsed) return null
  const round = (value: number) => Math.round(value * 1e7) / 1e7
  return JSON.stringify({ ...parsed.metadata, latitude: round(latitude), longitude: round(longitude) }, null, 2)
}

/**
 * The address parts of the optional JSON that geocoding can use.
 * @param metadataText - the textarea's content.
 * @returns street and project when they are text, else empty.
 */
export function addressFromMetadata(metadataText: string): { street?: string; project?: string } {
  const parsed = parseMetadataText(metadataText)
  if ("error" in parsed) return {}
  const text = (value: unknown) => (typeof value === "string" && value.trim() ? value.trim() : undefined)
  return { street: text(parsed.metadata.street), project: text(parsed.metadata.project) }
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
