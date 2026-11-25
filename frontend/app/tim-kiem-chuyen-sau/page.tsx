"use client"

import { useState } from "react"
import { Header } from "@/components/header"
import { SidebarFilter } from "@/components/SidebarFilter"
import { AIChat } from "@/components/ai-chat"
import { AMENITIES } from "@/lib/amenities"
import { cn } from "@/lib/utils"

export default function AdvancedSearchPage() {
  // State lifting: track filters from SidebarFilter and pass to AIChat
  const [currentFilters, setCurrentFilters] = useState<any>({})
  const [filtersOpen, setFiltersOpen] = useState(false)

  const handleApplyFilters = (filters: any) => {
    setCurrentFilters(filters)
  }

  // Helper function to format filter summary in Vietnamese
  const formatFilterSummary = (filters: any): string[] => {
    const summary: string[] = []

    // Price range
    if (filters.min_price || filters.max_price) {
      const minPrice = filters.min_price || 0
      const maxPrice = filters.max_price || "Không giới hạn"
      summary.push(`Giá: ${minPrice.toLocaleString("vi-VN")} – ${maxPrice === 999999 ? "Không giới hạn" : maxPrice.toLocaleString("vi-VN")} tỷ`)
    }

    // Area range
    if (filters.min_area || filters.max_area) {
      const minArea = filters.min_area || 0
      const maxArea = filters.max_area || "Không giới hạn"
      summary.push(`Diện tích: ${minArea} – ${maxArea === 999999 ? "Không giới hạn" : maxArea} m²`)
    }

    // Bedrooms
    if (filters.bedrooms && filters.bedrooms !== "0") {
      summary.push(`${filters.bedrooms}+ phòng ngủ`)
    }

    // Bathrooms
    if (filters.bathrooms && filters.bathrooms !== "0") {
      summary.push(`${filters.bathrooms}+ phòng tắm`)
    }

    // District
    if (filters.district && filters.district.trim() !== "") {
      summary.push(`Khu vực: ${filters.district}`)
    }

    // Property type
    if (filters.property_type && filters.property_type.trim() !== "") {
      summary.push(`Loại BĐS: ${filters.property_type}`)
    }

    // Legal status
    if (filters.legal_status && filters.legal_status.trim() !== "") {
      summary.push(`Pháp lý: ${filters.legal_status}`)
    }

    // Furniture
    if (filters.furniture && filters.furniture.trim() !== "") {
      summary.push(`Nội thất: ${filters.furniture}`)
    }

    // House direction
    if (filters.house_direction && filters.house_direction.trim() !== "") {
      summary.push(`Hướng nhà: ${filters.house_direction}`)
    }

    // Balcony direction
    if (filters.balcony_direction && filters.balcony_direction.trim() !== "") {
      summary.push(`Hướng ban công: ${filters.balcony_direction}`)
    }

    // Amenities are applied as individual boolean flags named by their backend key.
    const amenities = AMENITIES.filter(({ key }) => filters[key]).map(({ label }) => label)

    if (amenities.length > 0) {
      summary.push(`Tiện ích: ${amenities.join(", ")}`)
    }

    return summary
  }

  const filterSummary = formatFilterSummary(currentFilters)

  return (
    <div className="h-dvh overflow-hidden flex flex-col bg-background">
      <div className="shrink-0">
        <Header />
      </div>

      <main className="flex-1 min-h-0 flex overflow-hidden">
        {/* Criteria panel: a drawer on mobile, a fixed-width column on desktop. */}
        <aside
          className={cn(
            "lg:static lg:block lg:w-[340px] lg:shrink-0 lg:border-r lg:border-border lg:bg-transparent lg:p-4",
            filtersOpen ? "fixed inset-0 z-50 bg-black/40" : "hidden",
          )}
          onClick={(e) => {
            if (e.target === e.currentTarget) setFiltersOpen(false)
          }}
        >
          <div className="absolute inset-y-0 left-0 w-[88%] max-w-sm p-3 lg:static lg:h-full lg:w-auto lg:max-w-none lg:p-0">
            <SidebarFilter
              onApplyFilters={handleApplyFilters}
              onClose={() => setFiltersOpen(false)}
              showSupportCard={false}
              title="Tiêu chí tư vấn"
              applyLabel="Áp dụng cho trợ lý"
            />
          </div>
        </aside>

        <section className="flex-1 min-w-0 h-full overflow-hidden">
          <AIChat filters={currentFilters} criteria={filterSummary} onEditCriteria={() => setFiltersOpen(true)} />
        </section>
      </main>
    </div>
  )
}
