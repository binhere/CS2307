// Single source for the amenity flags a listing carries and the Vietnamese label shown
// for each, so the filter chips, the AI-advisor criteria summary and the detail page
// always name a flag the same way. Icons live with the detail page, the only place that
// draws them, to keep lucide icons out of the search-page bundle.

/**
 * Value sent as `property_type` when the user picks "Tất cả": the listing API takes a
 * comma-joined list, and an unchanged default does not count as an active filter.
 */
export const DEFAULT_PROPERTY_TYPE = "Căn hộ,Nhà đất"

/**
 * Every amenity flag, in display order. `key` is the boolean column / query parameter
 * name used by the backend; `label` is the text shown to the user.
 */
export const AMENITIES = [
  { key: "pool", label: "Hồ bơi" },
  { key: "gym", label: "Phòng gym" },
  { key: "park", label: "Khuôn viên" },
  { key: "bbq", label: "Khu BBQ" },
  { key: "kids_playground", label: "Khu vui chơi trẻ em" },
  { key: "sports_court", label: "Sân thể thao" },
  { key: "security_24h", label: "Bảo vệ 24/7" },
  { key: "reception", label: "Lễ tân" },
  { key: "elevator", label: "Thang máy" },
  { key: "parking", label: "Bãi đỗ xe" },
  { key: "near_metro", label: "Gần Metro" },
  { key: "near_bus", label: "Gần xe buýt" },
  { key: "near_highway", label: "Gần đường cao tốc" },
  { key: "near_school", label: "Gần trường học" },
  { key: "near_hospital", label: "Gần bệnh viện" },
  { key: "near_mall", label: "Gần trung tâm thương mại" },
  { key: "near_market", label: "Gần chợ" },
  { key: "near_park", label: "Gần công viên" },
  { key: "river_view", label: "View sông" },
  { key: "park_view", label: "View công viên" },
  { key: "city_view", label: "View thành phố" },
  { key: "balcony", label: "Ban công" },
  { key: "garden", label: "Vườn" },
  { key: "garage", label: "Garage" },
  { key: "terrace", label: "Sân thượng" },
] as const

/** Name of one amenity flag, e.g. "pool" or "near_mall". */
export type AmenityKey = (typeof AMENITIES)[number]["key"]
