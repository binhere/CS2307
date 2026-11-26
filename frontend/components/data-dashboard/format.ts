// Number formatting and Vietnamese labels for the data dashboard. The API returns codes
// (field names, feature keys, reason codes); every user-facing word lives here.

const NUMBER_FORMATS = new Map<number, Intl.NumberFormat>()

/**
 * Format a number the Vietnamese way (1.234,5).
 * @param value - number to format.
 * @param digits - maximum fraction digits.
 * @returns the formatted number.
 */
export function formatNumber(value: number, digits = 0): string {
  let format = NUMBER_FORMATS.get(digits)
  if (!format) {
    format = new Intl.NumberFormat("vi-VN", { maximumFractionDigits: digits })
    NUMBER_FORMATS.set(digits, format)
  }
  return format.format(value)
}

/**
 * Format a share (0–1) as a percentage, e.g. 0.583 → "58,3%".
 * @param share - value between 0 and 1.
 * @param digits - fraction digits of the percentage.
 * @returns the percentage text.
 */
export function formatShare(share: number, digits = 1): string {
  return `${formatNumber(share * 100, digits)}%`
}

/**
 * Format an ISO timestamp as "14:30 ngày 27/11" in the reader's time zone.
 * @param value - ISO 8601 timestamp.
 * @returns the formatted moment, or the input when it does not parse.
 */
export function formatMoment(value: string): string {
  const moment = new Date(value)
  if (Number.isNaN(moment.getTime())) return value
  const twoDigits = (part: number) => String(part).padStart(2, "0")
  return `${twoDigits(moment.getHours())}:${twoDigits(moment.getMinutes())} ngày ${twoDigits(moment.getDate())}/${twoDigits(moment.getMonth() + 1)}`
}

/** Catalog field codes from the quality section, in Vietnamese. */
export const FIELD_LABELS: Record<string, string> = {
  price_range: "Giá rao",
  area: "Diện tích",
  bedrooms: "Phòng ngủ",
  bathrooms: "Phòng tắm",
  coordinates: "Tọa độ",
  district: "Phường",
  legal_status: "Pháp lý",
  furnishing: "Nội thất",
  house_direction: "Hướng nhà",
  posted_date: "Ngày đăng",
  images: "Ảnh",
  description: "Mô tả",
  embedding: "Vector",
}

/** What makes a value invalid, per field (only fields with a rule). */
export const INVALID_RULES: Record<string, string> = {
  price_range: "≤ 0",
  area: "≤ 0",
  bedrooms: "ngoài 0–20",
  bathrooms: "ngoài 0–20",
  coordinates: "ngoài TP.HCM",
  posted_date: "sau hôm nay",
}

/** Drift feature codes, in Vietnamese with their unit. */
export const FEATURE_LABELS: Record<string, string> = {
  price: "Giá rao (tỷ)",
  area: "Diện tích (m²)",
  bedrooms: "Phòng ngủ",
  price_per_m2: "Đơn giá (triệu/m²)",
  property_group: "Nhóm loại BĐS",
  legal_status: "Pháp lý",
  ward: "Phường (10 phường nhiều tin nhất)",
}

/** Reasons the price model refuses a listing, in Vietnamese. */
export const REFUSAL_LABELS: Record<string, string> = {
  missing_fields: "Thiếu diện tích, phòng ngủ, giá hoặc tọa độ",
  outside_city: "Tọa độ ngoài TP.HCM",
  unknown_ward: "Phường chưa có trong dữ liệu huấn luyện",
  area_range: "Diện tích ngoài khoảng huấn luyện",
  bedrooms_range: "Số phòng ngủ ngoài khoảng huấn luyện",
  price_range: "Giá rao ngoài khoảng huấn luyện",
  price_per_m2_range: "Đơn giá ngoài khoảng huấn luyện",
}

/** Drift readings of a PSI value. */
export const DRIFT_STATUS_LABELS: Record<string, string> = {
  stable: "Ổn định",
  moderate: "Lệch vừa",
  major: "Lệch mạnh",
}

/** Model segments, in Vietnamese. */
export const SEGMENT_LABELS: Record<string, string> = {
  all: "Tất cả",
  "Căn hộ": "Căn hộ",
  "Nhà đất": "Nhà đất",
}

/** Where training rows came from in data/v3. */
export const SOURCE_LABELS: Record<string, string> = {
  new: "Crawl hiện tại",
  both: "Có ở cả hai",
  old: "Dataset cũ",
}
