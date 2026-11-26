// Tests for the listing form's pure parts: assembling the document from inputs and the optional
// JSON, writing geocoded coordinates back into that JSON, image fitting, card thumbnails and the
// post-login redirect (which must never leave the site).
// Run with: npm test (node --test, TypeScript sources loaded with --experimental-strip-types).
import assert from "node:assert/strict"
import { test } from "node:test"

const submission = await import("./listing-submission.ts")
const { fitWithin } = await import("./image-resize.ts")
const { getPropertyThumbnailSrc } = await import("./property-images.ts")

const REQUIRED = {
  title: " Bán căn hộ 2PN ", description: "Mô tả", property_type: "can_ho_chung_cu", price_billion_vnd: "4,5 tỷ",
  area_m2: "", ward: "Phường Cát Lái", former_district: "", posted_date: "2026-11-26",
}

test("blank required fields are left out so the server can report or fill them", () => {
  const result = submission.buildListingDocument(REQUIRED, '{"bedroom_count": 2}')
  assert.deepEqual(result.document, {
    title: "Bán căn hộ 2PN", description: "Mô tả", property_type: "can_ho_chung_cu", price_billion_vnd: "4,5 tỷ",
    ward: "Phường Cát Lái", posted_date: "2026-11-26", metadata: { bedroom_count: 2 },
  })
})

test("the optional JSON must be one object", () => {
  assert.deepEqual(submission.parseMetadataText("  "), { metadata: {} })
  assert.match(submission.parseMetadataText("[1, 2]").error, /object/)
  assert.match(submission.buildListingDocument(REQUIRED, "{bedroom_count: 2}").error, /JSON/)
})

test("a location picked on the map is written into the JSON without losing other fields", () => {
  const text = submission.withLocation('{"bedroom_count": 2, "street": "cũ"}', { latitude: 10.77921234567, longitude: 106.7571, street: "Đường Số 60" })
  assert.deepEqual(JSON.parse(text), { bedroom_count: 2, street: "Đường Số 60", latitude: 10.7792123, longitude: 106.7571 })
  assert.deepEqual(submission.coordinatesFromMetadata(text), { latitude: 10.7792123, longitude: 106.7571 })
  assert.deepEqual(JSON.parse(submission.withLocation(text, null)), { bedroom_count: 2, street: "Đường Số 60" })
  assert.equal(submission.withLocation('{"latitude": 1, "longitude": 2}', null), "")
  assert.equal(submission.withLocation("not json", { latitude: 1, longitude: 2 }), null)
  assert.equal(submission.coordinatesFromMetadata('{"latitude": "10.7"}'), null)
})

test("photos shrink to fit and are never enlarged", () => {
  assert.deepEqual(fitWithin(4032, 3024, 1280), { width: 1280, height: 960 })
  assert.deepEqual(fitWithin(3024, 4032, 480), { width: 360, height: 480 })
  assert.deepEqual(fitWithin(800, 600, 1280), { width: 800, height: 600 })
})

test("cards show a posted listing's thumbnail and a catalog photo as it is", () => {
  assert.equal(
    getPropertyThumbnailSrc("images/user-listings/9000000001/gallery/001.jpg"),
    `/api/property-images?path=${encodeURIComponent("images/user-listings/9000000001/thumbnails/001.jpg")}`,
  )
  assert.equal(
    getPropertyThumbnailSrc("images/Nhà riêng/Phường A/1/gallery/001.jpg"),
    `/api/property-images?path=${encodeURIComponent("images/Nhà riêng/Phường A/1/gallery/001.jpg")}`,
  )
})

test("after signing in, only paths on this site are followed", () => {
  assert.equal(submission.safeRedirectPath("/dang-tin"), "/dang-tin")
  for (const unsafe of ["https://evil.example", "//evil.example", "/\\evil.example", "", null]) {
    assert.equal(submission.safeRedirectPath(unsafe), null)
  }
})

test("the default posted date is today in Vietnam", () => {
  assert.equal(submission.todayInVietnam(new Date("2026-11-26T18:30:00Z")), "2026-11-27")
})

test("each reported problem lands under the input it is about", () => {
  const problem = (field, code) => ({ field, label: field, value: null, code, message: code })
  const report = {
    index: 0, reference: null, is_valid: false, listing: {},
    errors: [problem("title", "TOO_SHORT"), problem("former_district", "MISMATCHED_FORMER_DISTRICT")],
    warnings: [problem("metadata.colour", "UNKNOWN_FIELD"), problem("latitude", "COORDINATES_OUTSIDE_WARD")],
    conversions: [{ field: "price_billion_vnd", kind: "value", from: "4 tỷ 500", to: 4.5 }, { field: "bedroom_count", kind: "field", from: "metadata.pn", to: "bedroom_count" }],
  }
  const feedback = submission.feedbackByArea(report)
  assert.deepEqual(feedback.title.errors.map((item) => item.code), ["TOO_SHORT"])
  assert.deepEqual(feedback.former_district.errors.map((item) => item.code), ["MISMATCHED_FORMER_DISTRICT"])
  assert.deepEqual(feedback.metadata.warnings.map((item) => item.code), ["UNKNOWN_FIELD", "COORDINATES_OUTSIDE_WARD"])
  assert.equal(feedback.price_billion_vnd.conversions[0].to, 4.5)
  assert.equal(feedback.metadata.conversions[0].field, "bedroom_count")
  assert.deepEqual(feedback.ward, { errors: [], warnings: [], conversions: [] })
  assert.deepEqual(submission.feedbackByArea(null).metadata, { errors: [], warnings: [], conversions: [] })
})

const REFERENCE = {
  wards: ["Phường Cát Lái", "Phường Chánh Hiệp", "Phường Bình Dương", "Xã Nhà Bè"],
  former_districts: ["Huyện Nhà Bè", "Quận 2", "Thành phố Thủ Dầu Một", "Thành phố Thủ Đức", "Thành phố Tân Uyên"],
  ward_former_districts: {
    "Phường Cát Lái": ["Thành phố Thủ Đức"], "Phường Chánh Hiệp": ["Thành phố Thủ Dầu Một"],
    "Phường Bình Dương": ["Thành phố Thủ Dầu Một", "Thành phố Tân Uyên"], "Xã Nhà Bè": ["Huyện Nhà Bè"],
  },
  former_district_groups: {
    "Huyện Nhà Bè": "Huyện Nhà Bè", "Quận 2": "Thành phố Thủ Đức", "Thành phố Thủ Dầu Một": "Thành phố Thủ Dầu Một",
    "Thành phố Thủ Đức": "Thành phố Thủ Đức", "Thành phố Tân Uyên": "Thành phố Tân Uyên",
  },
}

test("choosing a ward narrows the former districts to the ones it came from", () => {
  assert.deepEqual(submission.formerDistrictOptions(REFERENCE, "Phường Chánh Hiệp"), ["Thành phố Thủ Dầu Một"])
  assert.deepEqual(submission.formerDistrictOptions(REFERENCE, "Phường Cát Lái"), ["Quận 2", "Thành phố Thủ Đức"])
  assert.equal(submission.formerDistrictOptions(REFERENCE, "phuong cat la").length, 5) // not a ward name yet
})

test("choosing a former district narrows the wards to the ones made from it", () => {
  assert.deepEqual(submission.wardOptions(REFERENCE, "Thành phố Thủ Dầu Một"), ["Phường Chánh Hiệp", "Phường Bình Dương"])
  assert.deepEqual(submission.wardOptions(REFERENCE, "Quận 2"), ["Phường Cát Lái"])
  assert.equal(submission.wardOptions(REFERENCE, "").length, 4)
})

test("a former district that no longer fits the ward is replaced or cleared", () => {
  assert.equal(submission.scopedFormerDistrict(REFERENCE, "Phường Chánh Hiệp", "Huyện Nhà Bè"), "Thành phố Thủ Dầu Một")
  assert.equal(submission.scopedFormerDistrict(REFERENCE, "Phường Cát Lái", "Quận 2"), "Quận 2")
  assert.equal(submission.scopedFormerDistrict(REFERENCE, "Phường Bình Dương", "Huyện Nhà Bè"), "")
  assert.equal(submission.scopedFormerDistrict(REFERENCE, "phuong lạ", "Huyện Nhà Bè"), "Huyện Nhà Bè")
})
