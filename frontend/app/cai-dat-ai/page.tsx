import type { Metadata } from "next"
import { Footer } from "@/components/footer"
import { Header } from "@/components/header"
import { LlmSettingsForm } from "@/components/llm-settings-form"

export const metadata: Metadata = {
  title: "Cài đặt AI",
}

/**
 * "Cài đặt AI" page where users bring their own LLM API key for the AI advisor.
 * Server-rendered shell; the form itself is a client component because it reads Web Storage.
 * @returns the page element.
 */
export default function LlmSettingsPage() {
  return (
    <div className="min-h-screen flex flex-col bg-background">
      <header className="sticky top-0 z-40 w-full">
        <Header />
      </header>

      <main className="flex-1 container mx-auto px-4 py-10">
        <div className="mx-auto w-full max-w-md">
          <h1 className="text-2xl font-bold text-foreground">Cài đặt AI</h1>
          <p className="mt-1 mb-6 text-sm text-muted-foreground text-pretty">
            Tư vấn AI dùng API key của bạn. Key chỉ lưu trong trình duyệt này, tự xóa sau 24 giờ và chỉ được gửi
            kèm các yêu cầu AI; máy chủ không lưu key.
          </p>
          <LlmSettingsForm />
        </div>
      </main>

      <Footer />
    </div>
  )
}
