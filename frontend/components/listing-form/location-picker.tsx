"use client"

// Picks a listing's location: an address search with suggestions (Photon) and an OpenStreetMap map
// where a click or a dragged pin sets the exact point. The parent turns the point into a ward and
// former district (from the official boundaries) and writes it into the listing.

import "leaflet/dist/leaflet.css"
import { useEffect, useId, useRef, useState } from "react"
import type { LeafletMouseEvent, Map as LeafletMap, Marker } from "leaflet"
import { Loader2, MapPin, Search, X } from "lucide-react"
import { Input } from "@/components/ui/input"
import { CITY_CENTRE, OSM_ATTRIBUTION, searchPlaces, streetAt, type FoundPlace } from "@/lib/geocoding"
import { isAbortError } from "@/lib/http"
import type { PickedLocation } from "@/lib/listing-submission"
import { cn } from "@/lib/utils"

const SEARCH_DELAY_MS = 350
const CITY_ZOOM = 12
const PICKED_ZOOM = 17
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"

/**
 * Address search plus map for one listing location.
 * @param props.value - the current point (shown as the pin), or null.
 * @param props.onPick - called with a picked point and, when known, its street.
 * @param props.onClear - called when the user removes the point.
 * @param props.status - one line about the point (its ward, or why it cannot be used), shown under the map.
 * @returns the picker element.
 */
export function LocationPicker({ value, onPick, onClear, status }: {
  value: { latitude: number; longitude: number } | null
  onPick: (location: PickedLocation) => void
  onClear: () => void
  status?: string
}) {
  const searchId = useId()
  const resultsId = `${searchId}-results`
  const [query, setQuery] = useState("")
  const [places, setPlaces] = useState<FoundPlace[]>([])
  const [searchState, setSearchState] = useState<"idle" | "searching" | "empty" | "error">("idle")
  const mapElement = useRef<HTMLDivElement>(null)
  const map = useRef<LeafletMap | null>(null)
  const marker = useRef<Marker | null>(null)
  const leafletModule = useRef<typeof import("leaflet") | null>(null)
  const [isMapReady, setMapReady] = useState(false)
  const streetLookup = useRef<AbortController | null>(null)
  // The label of the suggestion just chosen fills the search box; it must not search again.
  const chosenLabel = useRef<string | null>(null)
  // Map events are bound once; they reach the latest callback through this ref.
  const onPickRef = useRef(onPick)
  onPickRef.current = onPick

  /**
   * A point chosen on the map: report it at once, then again with its street when that is found.
   * @param latitude - decimal degrees.
   * @param longitude - decimal degrees.
   */
  const pickOnMap = (latitude: number, longitude: number) => {
    onPickRef.current({ latitude, longitude })
    streetLookup.current?.abort()
    const controller = new AbortController()
    streetLookup.current = controller
    void streetAt(latitude, longitude, controller.signal).then((street) => {
      if (street && !controller.signal.aborted) onPickRef.current({ latitude, longitude, street })
    })
  }
  const pickOnMapRef = useRef(pickOnMap)
  pickOnMapRef.current = pickOnMap

  // Leaflet touches window when imported, so it is loaded in the browser only.
  useEffect(() => {
    let isCancelled = false
    void import("leaflet").then((leaflet) => {
      if (isCancelled || !mapElement.current || map.current) return
      leafletModule.current = leaflet
      const created = leaflet.map(mapElement.current, { scrollWheelZoom: false })
        .setView([CITY_CENTRE.latitude, CITY_CENTRE.longitude], CITY_ZOOM)
      leaflet.tileLayer(TILE_URL, { maxZoom: 19, attribution: OSM_ATTRIBUTION }).addTo(created)
      created.on("click", (event: LeafletMouseEvent) => pickOnMapRef.current(event.latlng.lat, event.latlng.lng))
      map.current = created
      setMapReady(true)
    })
    return () => {
      isCancelled = true
      streetLookup.current?.abort()
      map.current?.remove()
      map.current = null
      marker.current = null
    }
  }, [])

  // Keep the pin where the listing is.
  useEffect(() => {
    const leaflet = leafletModule.current
    if (!isMapReady || !leaflet || !map.current) return
    if (!value) {
      marker.current?.remove()
      marker.current = null
      return
    }
    const position: [number, number] = [value.latitude, value.longitude]
    if (!marker.current) {
      // A CSS pin: Leaflet's default icon images do not survive bundling.
      const icon = leaflet.divIcon({ className: "", html: '<span class="listing-map-pin"></span>', iconSize: [24, 24], iconAnchor: [12, 24] })
      marker.current = leaflet.marker(position, { draggable: true, icon, keyboard: false }).addTo(map.current)
      marker.current.on("dragend", () => {
        const moved = marker.current?.getLatLng()
        if (moved) pickOnMapRef.current(moved.lat, moved.lng)
      })
    } else {
      marker.current.setLatLng(position)
    }
  }, [value, isMapReady])

  // Suggestions while typing: wait for a pause, and cancel the previous search.
  useEffect(() => {
    if (query === chosenLabel.current || query.trim().length < 3) {
      setPlaces([])
      setSearchState("idle")
      return
    }
    const controller = new AbortController()
    const timer = setTimeout(() => {
      setSearchState("searching")
      searchPlaces(query, controller.signal)
        .then((found) => {
          setPlaces(found)
          setSearchState(found.length ? "idle" : "empty")
        })
        .catch((error) => {
          if (!isAbortError(error)) setSearchState("error")
        })
    }, SEARCH_DELAY_MS)
    return () => {
      clearTimeout(timer)
      controller.abort()
    }
  }, [query])

  /**
   * A suggestion was chosen: move the map there and report it.
   * @param place - the chosen suggestion.
   */
  const choose = (place: FoundPlace) => {
    setPlaces([])
    chosenLabel.current = place.label
    setQuery(place.label)
    map.current?.setView([place.latitude, place.longitude], PICKED_ZOOM)
    onPick({ latitude: place.latitude, longitude: place.longitude, street: place.street })
  }

  return (
    <div className="space-y-3">
      <div className="relative">
        <label htmlFor={searchId} className="text-sm font-medium text-foreground">Tìm địa chỉ</label>
        <div className="relative mt-1.5">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" aria-hidden />
          <Input id={searchId} value={query} autoComplete="off" placeholder="Tên đường, dự án hoặc địa điểm" className="pl-9"
            aria-controls={resultsId} aria-expanded={places.length > 0} onChange={(event) => setQuery(event.target.value)} />
          {searchState === "searching" && (
            <Loader2 className="absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 animate-spin text-muted-foreground motion-reduce:animate-none" aria-hidden />
          )}
        </div>
        <ul id={resultsId} aria-label="Gợi ý địa chỉ"
          className={cn("absolute z-20 mt-1 w-full overflow-hidden rounded-md border border-border bg-card shadow-md", places.length === 0 && "hidden")}>
          {places.map((place) => (
            <li key={`${place.latitude},${place.longitude},${place.label}`}>
              <button type="button" onClick={() => choose(place)}
                className="flex w-full items-start gap-2 px-3 py-2 text-left text-sm hover:bg-muted focus-visible:bg-muted focus-visible:outline-none">
                <MapPin className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />{place.label}
              </button>
            </li>
          ))}
        </ul>
        <p role="status" className="mt-1 text-xs text-muted-foreground">
          {searchState === "empty" ? "Không tìm thấy địa chỉ này trong TP.HCM." : searchState === "error" ? "Lỗi khi tìm địa chỉ. Vui lòng thử lại." : ""}
        </p>
      </div>
      {/* isolate: Leaflet's panes use z-index 400+, which must not cover the sticky header. */}
      <div ref={mapElement} className="isolate h-72 w-full overflow-hidden rounded-lg border border-border bg-muted sm:h-80"
        role="application" aria-label="Bản đồ chọn vị trí: bấm vào bản đồ hoặc kéo ghim" />
      <div className="flex flex-wrap items-center justify-between gap-2 text-xs">
        <p className="text-muted-foreground" role="status">
          {value
            ? <>{status && <span className="text-foreground">{status} · </span>}{value.latitude.toFixed(6)}, {value.longitude.toFixed(6)}</>
            : "Chọn một gợi ý hoặc bấm vào bản đồ để đặt vị trí."}
        </p>
        {value && (
          <button type="button" onClick={onClear}
            className="inline-flex items-center gap-1 rounded px-1 text-muted-foreground hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
            <X className="h-3.5 w-3.5" aria-hidden />Xóa vị trí
          </button>
        )}
      </div>
    </div>
  )
}
