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

test("geocoded coordinates are written into the JSON without losing other fields", () => {
  const text = submission.withCoordinates('{"street": "Đường Số 60"}', 10.77921234567, 106.7571)
  assert.deepEqual(JSON.parse(text), { street: "Đường Số 60", latitude: 10.7792123, longitude: 106.7571 })
  assert.equal(submission.withCoordinates("not json", 1, 2), null)
  assert.deepEqual(submission.addressFromMetadata(text), { street: "Đường Số 60", project: undefined })
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
