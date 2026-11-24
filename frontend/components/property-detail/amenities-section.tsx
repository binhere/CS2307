'use client'

import { DetailSection } from '@/components/property-detail/section'
import {
  Waves,
  Dumbbell,
  TreePine,
  Flame,
  Play,
  Trophy,
  Lock,
  Clock,
  ArrowUp,
  ParkingCircle,
  Train,
  Bus,
  Zap,
  School,
  Hospital,
  ShoppingCart,
  Store,
  Leaf,
  Sprout,
  Building2,
  DoorOpen,
  Trees,
  Warehouse,
  LayoutGrid,
  type LucideIcon,
} from 'lucide-react'
import { AMENITIES, type AmenityKey } from '@/lib/amenities'

interface AmenitiesSectionProps {
  /** Listing with one boolean per amenity flag; missing flags count as absent. */
  property: Partial<Record<AmenityKey, boolean | null>>
}

/** Icon drawn next to each amenity label; typed by AmenityKey so a new flag cannot lack one. */
const AMENITY_ICONS: Record<AmenityKey, LucideIcon> = {
  pool: Waves,
  gym: Dumbbell,
  park: TreePine,
  bbq: Flame,
  kids_playground: Play,
  sports_court: Trophy,
  security_24h: Lock,
  reception: Clock,
  elevator: ArrowUp,
  parking: ParkingCircle,
  near_metro: Train,
  near_bus: Bus,
  near_highway: Zap,
  near_school: School,
  near_hospital: Hospital,
  near_mall: ShoppingCart,
  near_market: Store,
  near_park: Leaf,
  river_view: Waves,
  park_view: Sprout,
  city_view: Building2,
  balcony: DoorOpen,
  garden: Trees,
  garage: Warehouse,
  terrace: LayoutGrid,
}

export function AmenitiesSection({ property }: AmenitiesSectionProps) {
  const activeAmenities = AMENITIES.filter((amenity) => property[amenity.key])

  if (activeAmenities.length === 0) return null

  return (
    <DetailSection title="Tiện ích nội/ngoại khu" description={`${activeAmenities.length} tiện ích nổi bật`}>
      <ul className="grid grid-cols-2 gap-2 md:grid-cols-3">
        {activeAmenities.map((amenity) => {
          const Icon = AMENITY_ICONS[amenity.key]
          return (
            <li key={amenity.key} className="flex items-center gap-2.5 rounded-lg bg-muted/60 px-3 py-2.5">
              <Icon className="h-4 w-4 flex-shrink-0 text-primary" />
              <span className="text-sm font-medium text-foreground">{amenity.label}</span>
            </li>
          )
        })}
      </ul>
    </DetailSection>
  )
}
