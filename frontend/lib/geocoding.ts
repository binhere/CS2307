// Address search for the listing form's map, done in the user's browser with Photon, an
// OpenStreetMap geocoder built for search-as-you-type (photon.komoot.io; fair use, no key).
// Results are limited to Ho Chi Minh City; the user then fine-tunes the pin on the map, and the
// server names the ward from the official boundaries. Zip uploads are geocoded on the server.

/** Photon's public endpoints. */
export const PHOTON_SEARCH_URL = "https://photon.komoot.io/api/"
export const PHOTON_REVERSE_URL = "https://photon.komoot.io/reverse"

/** Attribution the OpenStreetMap licence requires wherever its map or data is shown. */
export const OSM_ATTRIBUTION = "© OpenStreetMap contributors"

/** Ho Chi Minh City since 1 July 2025 (with Bình Dương and Bà Rịa - Vũng Tàu): west, south, east, north. */
const CITY_BOUNDING_BOX = "106.3,10.3,107.6,11.6"
/** The old city centre, where most listings are: results near it rank first. */
export const CITY_CENTRE = { latitude: 10.7769, longitude: 106.7009 }
const RESULT_LIMIT = 6

/** A place the user can pick. */
export interface FoundPlace {
  latitude: number
  longitude: number
  /** What the list shows, e.g. "Căn hộ DEFINE, Đường Số 60, Phường Cát Lái". */
  label: string
  /** The street, when the place is on one or is one. */
  street?: string
}

/** One feature of Photon's GeoJSON answer (only the properties used here). */
interface PhotonFeature {
  geometry?: { coordinates?: [number, number] }
  properties?: {
    name?: string
    housenumber?: string
    street?: string
    district?: string
    locality?: string
    city?: string
    osm_key?: string
  }
}

/**
 * The search URL for a typed address.
 * @param query - what the user typed.
 * @returns the Photon URL, limited to the city and biased towards its centre.
 */
export function buildSearchUrl(query: string): string {
  const url = new URL(PHOTON_SEARCH_URL)
  url.search = new URLSearchParams({
    // "default" keeps OpenStreetMap's own (Vietnamese) names instead of the browser's language.
    q: query.trim(), limit: String(RESULT_LIMIT), bbox: CITY_BOUNDING_BOX, lang: "default",
    lat: String(CITY_CENTRE.latitude), lon: String(CITY_CENTRE.longitude),
  }).toString()
  return url.toString()
}

/**
 * Turn Photon's answer into places to pick, dropping features without coordinates and duplicates.
 * @param body - the parsed JSON answer.
 * @returns the places, in Photon's order.
 */
export function parsePlaces(body: unknown): FoundPlace[] {
  const features = (body as { features?: PhotonFeature[] } | null)?.features
  if (!Array.isArray(features)) return []
  const places: FoundPlace[] = []
  const seen = new Set<string>()
  for (const feature of features) {
    const coordinates = feature.geometry?.coordinates
    if (!coordinates || !Number.isFinite(coordinates[0]) || !Number.isFinite(coordinates[1])) continue
    const properties = feature.properties ?? {}
    const street = properties.street ?? (properties.osm_key === "highway" ? properties.name : undefined)
    const streetLine = [properties.housenumber, properties.street].filter(Boolean).join(" ")
    const parts = [properties.name, streetLine, properties.district ?? properties.locality, properties.city]
    const label = parts.filter((part, index) => part && parts.indexOf(part) === index).join(", ")
    if (!label || seen.has(label)) continue
    seen.add(label)
    places.push({ latitude: coordinates[1], longitude: coordinates[0], label, street })
  }
  return places
}

/**
 * Search addresses as the user types. Call it debounced and abort the previous call.
 * @param query - at least 3 characters; shorter queries return no places without a request.
 * @param signal - aborts a superseded search.
 * @param fetchImplementation - fetch, replaceable in tests.
 * @returns places in the city.
 * @throws Error on a network or HTTP error (and an AbortError when aborted).
 */
export async function searchPlaces(
  query: string, signal?: AbortSignal, fetchImplementation: typeof fetch = (input, init) => fetch(input, init),
): Promise<FoundPlace[]> {
  if (query.trim().length < 3) return []
  const response = await fetchImplementation(buildSearchUrl(query), { signal })
  if (!response.ok) throw new Error(`Photon answered ${response.status}`)
  return parsePlaces(await response.json())
}

/**
 * The street at a point the user clicked or dragged the pin to.
 * @param latitude - decimal degrees.
 * @param longitude - decimal degrees.
 * @param signal - aborts a superseded lookup.
 * @param fetchImplementation - fetch, replaceable in tests.
 * @returns the street name, or undefined when there is none nearby or the lookup fails.
 */
export async function streetAt(
  latitude: number, longitude: number, signal?: AbortSignal,
  fetchImplementation: typeof fetch = (input, init) => fetch(input, init),
): Promise<string | undefined> {
  const url = new URL(PHOTON_REVERSE_URL)
  url.search = new URLSearchParams({ lat: String(latitude), lon: String(longitude), limit: "1", lang: "default" }).toString()
  try {
    const response = await fetchImplementation(url.toString(), { signal })
    if (!response.ok) return undefined
    return parsePlaces(await response.json())[0]?.street
  } catch {
    return undefined
  }
}
