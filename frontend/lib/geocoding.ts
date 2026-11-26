// Geocoding for the single-listing form, done in the user's browser: the form posts one listing,
// so it asks Nominatim (OpenStreetMap) directly; zip uploads are geocoded on the server instead.
// Nominatim's policy (operations.osmfoundation.org/policies/nominatim) allows at most one request
// per second, no search-as-you-type and requires attribution, so call this from a button, never
// from typing, and show OSM_ATTRIBUTION next to the result. The server checks the coordinates
// against the ward boundaries when the listing is submitted.

/** Public Nominatim search endpoint. */
export const NOMINATIM_SEARCH_URL = "https://nominatim.openstreetmap.org/search"

/** Attribution the OpenStreetMap licence requires wherever a geocoded result is shown. */
export const OSM_ATTRIBUTION = "© OpenStreetMap contributors"

/** Ho Chi Minh City since 1 July 2025 (with Bình Dương and Bà Rịa - Vũng Tàu): west, north, east, south. */
const CITY_VIEWBOX = "106.3,11.6,107.6,10.3"
const MINIMUM_INTERVAL_MS = 1000

/** A geocoded address. */
export interface GeocodedPlace {
  latitude: number
  longitude: number
  /** Nominatim's full name of the match, to show the user what was found. */
  label: string
}

/** The address parts the form has. */
export interface AddressParts {
  street?: string
  project?: string
  ward?: string
}

/**
 * The search text for an address, or null when there is nothing more precise than the ward
 * (geocoding a ward alone would return its centre and look like a real location).
 * @param parts - street, project and ward as typed.
 * @returns e.g. "Đường Số 60, Phường Cát Lái, Thành phố Hồ Chí Minh", or null.
 */
export function buildGeocodingQuery(parts: AddressParts): string | null {
  const precise = [parts.project, parts.street].map((part) => part?.trim()).filter(Boolean)
  if (precise.length === 0) return null
  return [...precise, parts.ward?.trim(), "Thành phố Hồ Chí Minh"].filter(Boolean).join(", ")
}

/** Dependencies of a geocoder, replaceable in tests. */
export interface GeocoderDependencies {
  fetchImplementation?: typeof fetch
  now?: () => number
  wait?: (milliseconds: number, signal?: AbortSignal) => Promise<void>
}

/**
 * Wait a number of milliseconds, ending early with an AbortError when the signal aborts.
 * @param milliseconds - time to wait.
 * @param signal - optional abort signal.
 * @returns a promise that resolves after the wait.
 */
function waitFor(milliseconds: number, signal?: AbortSignal): Promise<void> {
  return new Promise((resolve, reject) => {
    if (signal?.aborted) return reject(new DOMException("Aborted", "AbortError"))
    const timer = setTimeout(resolve, milliseconds)
    signal?.addEventListener("abort", () => {
      clearTimeout(timer)
      reject(new DOMException("Aborted", "AbortError"))
    }, { once: true })
  })
}

/**
 * Create a geocoder that keeps its own one-request-per-second pace.
 * @param dependencies - fetch, clock and wait; the browser's by default.
 * @returns a function that geocodes an address: it resolves to the best match in the city or null
 *   when there is none or the address has no street or project, and rejects on a network or HTTP
 *   error (and with an AbortError when the signal aborts).
 */
export function createGeocoder(dependencies: GeocoderDependencies = {}) {
  const fetchImplementation = dependencies.fetchImplementation ?? ((input, init) => fetch(input, init))
  const now = dependencies.now ?? (() => Date.now())
  const wait = dependencies.wait ?? waitFor
  let lastRequestAt: number | null = null

  return async function geocodeAddress(parts: AddressParts, signal?: AbortSignal): Promise<GeocodedPlace | null> {
    const query = buildGeocodingQuery(parts)
    if (!query) return null
    if (lastRequestAt !== null) {
      const remaining = MINIMUM_INTERVAL_MS - (now() - lastRequestAt)
      if (remaining > 0) await wait(remaining, signal)
    }
    lastRequestAt = now()
    const url = new URL(NOMINATIM_SEARCH_URL)
    url.search = new URLSearchParams({
      q: query, format: "jsonv2", limit: "1", countrycodes: "vn",
      viewbox: CITY_VIEWBOX, bounded: "1", "accept-language": "vi",
    }).toString()
    // Browsers send the page origin as Referer, which identifies this site to Nominatim.
    const response = await fetchImplementation(url.toString(), { signal, headers: { Accept: "application/json" } })
    if (!response.ok) throw new Error(`Nominatim answered ${response.status}`)
    const results: unknown = await response.json()
    if (!Array.isArray(results) || results.length === 0) return null
    const best = results[0] as { lat?: string; lon?: string; display_name?: string }
    const latitude = Number(best.lat)
    const longitude = Number(best.lon)
    if (!Number.isFinite(latitude) || !Number.isFinite(longitude)) return null
    return { latitude, longitude, label: best.display_name ?? query }
  }
}

/** The browser's geocoder for the listing form. */
export const geocodeAddress = createGeocoder()
