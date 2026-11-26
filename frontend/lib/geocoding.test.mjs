// Tests for the listing form's address search (Photon): searches stay inside the city, short
// queries cost no request, and answers become a clean list of places with their street.
// Run with: npm test (node --test, TypeScript sources loaded with --experimental-strip-types).
import assert from "node:assert/strict"
import { test } from "node:test"

const { buildSearchUrl, parsePlaces, searchPlaces, streetAt } = await import("./geocoding.ts")

const feature = (properties, coordinates = [106.7571, 10.7792]) => ({ geometry: { coordinates }, properties })

test("searches are limited to the city and biased to its centre", () => {
  const params = new URL(buildSearchUrl("  Đường Số 60 ")).searchParams
  assert.equal(params.get("q"), "Đường Số 60")
  assert.equal(params.get("bbox"), "106.3,10.3,107.6,11.6")
  assert.ok(params.get("lat") && params.get("lon"))
  assert.equal(params.get("lang"), "default") // Vietnamese names, not the browser's language
})

test("answers become places with a readable label and their street", () => {
  const places = parsePlaces({
    features: [
      feature({ name: "Căn hộ DEFINE", street: "Đường Số 60", district: "Phường Cát Lái", city: "Thành phố Hồ Chí Minh" }),
      feature({ name: "Đường Nguyễn Thị Định", osm_key: "highway", district: "Phường Cát Lái" }, [106.789, 10.7577]),
      feature({ name: "Căn hộ DEFINE", street: "Đường Số 60", district: "Phường Cát Lái", city: "Thành phố Hồ Chí Minh" }),
      { geometry: {}, properties: { name: "no coordinates" } },
    ],
  })
  assert.deepEqual(places, [
    { latitude: 10.7792, longitude: 106.7571, label: "Căn hộ DEFINE, Đường Số 60, Phường Cát Lái, Thành phố Hồ Chí Minh", street: "Đường Số 60" },
    { latitude: 10.7577, longitude: 106.789, label: "Đường Nguyễn Thị Định, Phường Cát Lái", street: "Đường Nguyễn Thị Định" },
  ])
  assert.deepEqual(parsePlaces(null), [])
})

test("short queries make no request and HTTP errors reject", async () => {
  let requests = 0
  const failing = async () => { requests += 1; return { ok: false, status: 429, json: async () => ({}) } }
  assert.deepEqual(await searchPlaces("ab", undefined, failing), [])
  assert.equal(requests, 0)
  await assert.rejects(searchPlaces("Đường Số 60", undefined, failing), /429/)
})

test("the street at a point comes from the reverse lookup, and failures give none", async () => {
  const answering = async () => ({ ok: true, status: 200, json: async () => ({ features: [feature({ name: "Đường Số 60", osm_key: "highway" })] }) })
  assert.equal(await streetAt(10.7792, 106.7571, undefined, answering), "Đường Số 60")
  const broken = async () => { throw new Error("offline") }
  assert.equal(await streetAt(10.7792, 106.7571, undefined, broken), undefined)
})
