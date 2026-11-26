// Tests for the listing form's geocoder: it asks Nominatim from the browser, so it must keep the
// public server's rules itself (one request per second, results bounded to the city) and never
// geocode a bare ward, whose centre would look like a real location.
// Run with: npm test (node --test, TypeScript sources loaded with --experimental-strip-types).
import assert from "node:assert/strict"
import { test } from "node:test"

const { buildGeocodingQuery, createGeocoder } = await import("./geocoding.ts")

/** A fetch stand-in that records URLs and answers from a queue of [status, body] pairs. */
function fakeFetch(answers) {
  const requests = []
  const fetchImplementation = async (url) => {
    requests.push(new URL(url))
    const [status, body] = answers.shift()
    return { ok: status < 400, status, json: async () => body }
  }
  return { requests, fetchImplementation }
}

test("the query needs a street or project and names the city", () => {
  assert.equal(buildGeocodingQuery({ ward: "Phường Cát Lái" }), null)
  assert.equal(
    buildGeocodingQuery({ street: " Đường Số 60 ", ward: "Phường Cát Lái" }),
    "Đường Số 60, Phường Cát Lái, Thành phố Hồ Chí Minh",
  )
})

test("a match is returned with its label, and the request is bounded to the city", async () => {
  const { requests, fetchImplementation } = fakeFetch([[200, [{ lat: "10.7792", lon: "106.7571", display_name: "Phường Cát Lái" }]]])
  const geocode = createGeocoder({ fetchImplementation, now: () => 0, wait: async () => {} })
  const place = await geocode({ street: "Đường Số 60", ward: "Phường Cát Lái" })
  assert.deepEqual(place, { latitude: 10.7792, longitude: 106.7571, label: "Phường Cát Lái" })
  const params = requests[0].searchParams
  assert.equal(params.get("bounded"), "1")
  assert.equal(params.get("countrycodes"), "vn")
  assert.equal(params.get("q"), "Đường Số 60, Phường Cát Lái, Thành phố Hồ Chí Minh")
})

test("a second request waits until a second has passed", async () => {
  let clock = 1000
  const waits = []
  const { fetchImplementation } = fakeFetch([[200, []], [200, []]])
  const geocode = createGeocoder({
    fetchImplementation,
    now: () => clock,
    wait: async (milliseconds) => { waits.push(milliseconds); clock += milliseconds },
  })
  assert.equal(await geocode({ street: "Đường A" }), null)
  clock += 300
  await geocode({ street: "Đường B" })
  assert.deepEqual(waits, [700])
})

test("an HTTP error rejects instead of looking like no match", async () => {
  const { fetchImplementation } = fakeFetch([[429, {}]])
  const geocode = createGeocoder({ fetchImplementation, now: () => 0, wait: async () => {} })
  await assert.rejects(geocode({ street: "Đường A" }), /429/)
})
