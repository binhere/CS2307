'use client'

import { useEffect, useState } from 'react'
import Link from 'next/link'
import { Phone, RefreshCw } from 'lucide-react'
import { useParams } from 'next/navigation'
import { Header } from '@/components/header'
import { Footer } from '@/components/footer'
import { PropertyDetailBreadcrumb } from '@/components/property-detail/breadcrumb'
import { ImageGallery } from '@/components/property-detail/image-gallery'
import { PropertyHeader } from '@/components/property-detail/property-header'
import { QuickSpecsGrid } from '@/components/property-detail/quick-specs-grid'
import { DescriptionSection } from '@/components/property-detail/description-section'
import { AmenitiesSection } from '@/components/property-detail/amenities-section'
import { LocationSection } from '@/components/property-detail/location-section'
import { PriceEstimateSection } from '@/components/property-detail/price-estimate-section'
import { KnowledgeGraphSection } from '@/components/property-detail/knowledge-graph-section'
import { ContactForm } from '@/components/property-detail/contact-form'
import { SimilarProperties } from '@/components/property-detail/similar-properties'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { sendInteraction } from '@/lib/feedback'
import { formatPrice, formatPricePerM2 } from '@/lib/format'
import { fetchJson, HttpError, isAbortError, type ApiEnvelope } from '@/lib/http'

interface PropertyData {
  id: string
  listing_id?: number | string
  title: string
  price_range: string
  area: number | null
  address: string
  location_link?: string | null
  latitude_longitude?: string | null
  description: string
  images: string[]
  listing_type: string
  bedrooms: number | null
  bathrooms: number | null
  legal_status: string | null
  furnishing: string | null
  balcony_direction: string | null
  house_direction: string | null
  posted_date: string
  pool: boolean
  gym: boolean
  park: boolean
  bbq: boolean
  kids_playground: boolean
  sports_court: boolean
  security_24h: boolean
  reception: boolean
  elevator: boolean
  parking: boolean
  near_metro: boolean
  near_bus: boolean
  near_highway: boolean
  near_school: boolean
  near_hospital: boolean
  near_mall: boolean
  near_market: boolean
  near_park: boolean
  river_view: boolean
  park_view: boolean
  city_view: boolean
  balcony: boolean
  garden: boolean
  garage: boolean
  terrace: boolean
}

/** Why the listing could not be shown: the backend has no such listing, or the request failed. */
type LoadFailure = 'not_found' | 'error'

export default function PropertyDetailPage() {
  const params = useParams<{ id: string }>()
  const propertyId = params?.id
  const [property, setProperty] = useState<PropertyData | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [loadFailure, setLoadFailure] = useState<LoadFailure | null>(null)
  const [retryCount, setRetryCount] = useState(0)

  useEffect(() => {
    if (!propertyId) return

    const controller = new AbortController()
    let cachedProperty: PropertyData | null = null
    setIsLoading(true)
    setLoadFailure(null)

    // Use the card payload for an instant first paint, but always refresh from
    // the backend so direct links and browser refreshes work as expected.
    const selectedProperty = sessionStorage.getItem('selectedProperty')
    if (selectedProperty) {
      try {
        const parsedProperty = JSON.parse(selectedProperty) as PropertyData
        const cachedIds = [parsedProperty.id, parsedProperty.listing_id].filter(Boolean).map(String)
        if (cachedIds.includes(String(propertyId))) cachedProperty = parsedProperty
      } catch (err) {
        console.error('Error parsing selected property:', err)
      }
    }
    const hasCachedProperty = cachedProperty !== null
    // Also clears the previous listing when navigating from one listing to another.
    setProperty(cachedProperty)
    if (hasCachedProperty) setIsLoading(false)

    fetchJson<ApiEnvelope<PropertyData>>(`/api/properties/${encodeURIComponent(propertyId)}`, {
      signal: controller.signal,
      cache: 'no-store',
    })
      .then((payload) => {
        // The backend answers an unknown id with 200 and `data: null` rather than a 404.
        if (!payload?.data) {
          if (!hasCachedProperty) setLoadFailure('not_found')
          return
        }
        setProperty(payload.data)
        sessionStorage.setItem('selectedProperty', JSON.stringify(payload.data))
      })
      .catch((error: unknown) => {
        if (isAbortError(error)) return
        // A card payload already on screen stays visible; only a blank page needs an error state.
        if (hasCachedProperty) return
        setLoadFailure(error instanceof HttpError && error.status === 404 ? 'not_found' : 'error')
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsLoading(false)
      })

    return () => controller.abort()
  }, [propertyId, retryCount])

  // Record a `view` on open and the accumulated dwell time on leave.
  const propertyListingKey = property ? String(property.listing_id ?? property.id) : null
  useEffect(() => {
    if (!propertyListingKey) return
    const listingId = propertyListingKey
    const openedAt = Date.now()
    return () => {
      const dwell = (Date.now() - openedAt) / 1000
      sendInteraction({
        listing_id: listingId,
        action_type: "view",
        source: "detail",
        dwell_time_seconds: dwell,
      })
    }
  }, [propertyListingKey])

  const shell = (content: React.ReactNode) => (
    <div className="min-h-screen flex flex-col bg-background">
      <header className="sticky top-0 z-40 w-full">
        <Header />
      </header>
      {content}
      <Footer />
    </div>
  )

  if (!isLoading && !property && loadFailure === 'error') {
    return shell(
      <main className="flex-1 container mx-auto px-4 py-16">
        <div className="mx-auto max-w-md text-center space-y-3">
          <h2 className="text-2xl font-bold text-foreground">Không thể tải thông tin bất động sản</h2>
          <p className="text-muted-foreground">Vui lòng thử lại sau.</p>
          <div className="mt-2 flex flex-wrap justify-center gap-2">
            <Button type="button" onClick={() => setRetryCount((count) => count + 1)}>
              <RefreshCw className="h-4 w-4" />
              Thử lại
            </Button>
            <Button asChild variant="outline">
              <Link href="/">Quay lại danh sách</Link>
            </Button>
          </div>
        </div>
      </main>,
    )
  }

  if (!isLoading && !property) {
    return shell(
      <main className="flex-1 container mx-auto px-4 py-16">
        <div className="mx-auto max-w-md text-center space-y-3">
          <h2 className="text-2xl font-bold text-foreground">Không tìm thấy bất động sản</h2>
          <p className="text-muted-foreground">Tin đăng có thể đã hết hạn hoặc bị gỡ. Hãy quay lại danh sách để xem các lựa chọn khác.</p>
          <Button asChild className="mt-2">
            <Link href="/">Quay lại danh sách</Link>
          </Button>
        </div>
      </main>,
    )
  }

  const pricePerM2 = property ? formatPricePerM2(property.price_range, property.area) : null

  // The estimate, graph and similar listings need only the id from the URL, so they mount
  // (and start fetching) alongside the listing request instead of waiting for it. They sit
  // in fixed positions so they are not remounted when the listing arrives.
  return shell(
    <>
      <main className="flex-1 container mx-auto px-4 py-6 pb-28 lg:pb-6">
        {property ? <PropertyDetailBreadcrumb property={property} /> : <Skeleton className="h-5 w-40" />}

        <div className="mt-4 grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_360px]">
          <div className="min-w-0 space-y-6">
            {property ? (
              <>
                <ImageGallery images={property.images} title={property.title} />
                <PropertyHeader property={property} />
                <QuickSpecsGrid property={property} />
                <DescriptionSection description={property.description} />
                <AmenitiesSection property={property} />
                <LocationSection
                  address={property.address}
                  locationLink={property.location_link}
                  latitudeLongitude={property.latitude_longitude}
                />
              </>
            ) : (
              <>
                <Skeleton className="aspect-[16/10] w-full rounded-xl" />
                <Skeleton className="h-40 w-full rounded-xl" />
              </>
            )}
            {propertyId && (
              <>
                <PriceEstimateSection key={`price-estimate-${propertyId}`} propertyId={propertyId} />
                <KnowledgeGraphSection key={`knowledge-graph-${propertyId}`} propertyId={propertyId} />
              </>
            )}
          </div>

          <aside className="lg:sticky lg:top-20 lg:self-start">
            {property ? (
              <ContactForm listingId={property.listing_id ?? property.id} />
            ) : (
              <Skeleton className="h-80 w-full rounded-xl" />
            )}
          </aside>
        </div>

        <div className="mt-10">
          <SimilarProperties key={propertyId} />
        </div>
      </main>

      {/* Mobile: keep price and the contact action reachable while scrolling. */}
      {property && (
        <div className="fixed inset-x-0 bottom-0 z-30 border-t border-border bg-card/95 px-4 py-3 backdrop-blur lg:hidden">
          <div className="flex items-center gap-3">
            <div className="min-w-0 flex-1">
              <p className="text-lg font-bold leading-tight text-price">{formatPrice(property.price_range)}</p>
              {pricePerM2 && <p className="text-xs text-muted-foreground">{pricePerM2}</p>}
            </div>
            <Button asChild className="h-11 px-5">
              <a href="#lien-he">
                <Phone className="h-4 w-4" />
                Liên hệ tư vấn
              </a>
            </Button>
          </div>
        </div>
      )}
    </>,
  )
}
