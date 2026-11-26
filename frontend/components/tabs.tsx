"use client"

// A row of tabs over panels that all stay mounted, so switching keeps what each panel holds
// (a half-filled form, a fetched dashboard). Follows the WAI-ARIA tabs pattern: arrow keys move
// between tabs, only the selected tab is in the tab order.

import { useId, useState, type KeyboardEvent, type ReactNode } from "react"
import { cn } from "@/lib/utils"

/** One tab and its panel. */
export interface TabItem {
  key: string
  label: string
  panel: ReactNode
}

/**
 * Tabs with persistent panels.
 * @param props.label - accessible name of the tab list, e.g. "Cách đăng tin".
 * @param props.items - the tabs, in order; the first is selected at first.
 * @returns the tab list followed by every panel, the unselected ones hidden.
 */
export function Tabs({ label, items }: { label: string; items: TabItem[] }) {
  const baseId = useId()
  const [selected, setSelected] = useState(items[0]?.key)

  /**
   * Arrow keys move the selection to the previous or next tab and focus it.
   * @param event - key event on a tab.
   */
  const moveBetweenTabs = (event: KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return
    event.preventDefault()
    const position = items.findIndex((item) => item.key === selected)
    const step = event.key === "ArrowRight" ? 1 : items.length - 1
    const next = items[(position + step) % items.length].key
    setSelected(next)
    document.getElementById(`${baseId}-${next}-tab`)?.focus()
  }

  return (
    <div className="space-y-6">
      <div role="tablist" aria-label={label} className="inline-flex max-w-full overflow-x-auto rounded-lg border border-border bg-muted p-1">
        {items.map((item) => (
          <button key={item.key} type="button" role="tab" id={`${baseId}-${item.key}-tab`} aria-controls={`${baseId}-${item.key}`}
            aria-selected={selected === item.key} tabIndex={selected === item.key ? 0 : -1}
            onClick={() => setSelected(item.key)} onKeyDown={moveBetweenTabs}
            className={cn("shrink-0 rounded-md px-4 py-1.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              selected === item.key ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>
            {item.label}
          </button>
        ))}
      </div>
      {items.map((item) => (
        <div key={item.key} role="tabpanel" id={`${baseId}-${item.key}`} aria-labelledby={`${baseId}-${item.key}-tab`}
          hidden={selected !== item.key}>
          {item.panel}
        </div>
      ))}
    </div>
  )
}
