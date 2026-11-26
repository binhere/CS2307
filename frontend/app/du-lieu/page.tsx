import type { Metadata } from "next"
import { DataDashboard } from "@/components/data-dashboard/data-dashboard"
import { Footer } from "@/components/footer"
import { Header } from "@/components/header"

export const metadata: Metadata = {
  title: "Dữ liệu",
}

/**
 * Public data dashboard: catalog health, data quality, drift and the price model's quality.
 * Server-rendered shell; the dashboard itself is a client component that fetches /api/monitoring.
 * @returns the page element.
 */
export default function DataPage() {
  return (
    <div className="min-h-screen flex flex-col bg-background">
      <header className="sticky top-0 z-40 w-full">
        <Header />
      </header>

      <main className="flex-1 container mx-auto px-4 py-10">
        <h1 className="mb-6 text-2xl font-bold text-foreground">Dữ liệu</h1>
        <DataDashboard />
      </main>

      <Footer />
    </div>
  )
}
