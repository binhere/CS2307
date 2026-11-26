import type { Metadata } from "next"
import { Footer } from "@/components/footer"
import { Header } from "@/components/header"
import { ListingForm } from "@/components/listing-form/listing-form"

export const metadata: Metadata = {
  title: "Đăng tin",
}

/**
 * Post a listing: server-rendered shell around the client form (sign-in required to post).
 * @returns the page element.
 */
export default function PostListingPage() {
  return (
    <div className="min-h-screen flex flex-col bg-background">
      <header className="sticky top-0 z-40 w-full">
        <Header />
      </header>

      <main className="flex-1 container mx-auto max-w-4xl px-4 py-10">
        <h1 className="text-2xl font-bold text-foreground">Đăng tin</h1>
        <p className="mt-1 mb-6 text-sm text-muted-foreground">Tin hiển thị ngay sau khi đăng.</p>
        <ListingForm />
      </main>

      <Footer />
    </div>
  )
}
