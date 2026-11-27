"use client"

import { AdvancedFilter } from "@/components/advanced-filter"

interface SidebarFilterProps {
  onApplyFilters?: (filters: any) => void
  onClose?: () => void
  title?: string
  applyLabel?: string
}

export function SidebarFilter({
  onApplyFilters,
  onClose,
  title,
  applyLabel,
}: SidebarFilterProps) {
  return (
    <div className="flex h-full flex-col gap-4">
      <div className="min-h-0 flex-1">
        <AdvancedFilter
          onApplyFilters={(filters) => onApplyFilters?.(filters)}
          onClose={onClose}
          title={title}
          applyLabel={applyLabel}
        />
      </div>
    </div>
  )
}
