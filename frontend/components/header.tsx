"use client"

import { BarChart3, Building2, KeyRound, LogIn, LogOut, Search, Sparkles, SquarePlus } from "lucide-react"
import { Button } from "@/components/ui/button"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useAuth } from "@/lib/auth-context"
import { useLlmSettingsStatus } from "@/lib/llm-settings"
import { cn } from "@/lib/utils"

/** Route of the bring-your-own-key settings page. */
const LLM_SETTINGS_PATH = "/cai-dat-ai"

const NAV_ITEMS = [
  { href: "/", label: "Tìm nhà", icon: Search },
  { href: "/tim-kiem-chuyen-sau", label: "Tư vấn AI", icon: Sparkles },
  { href: "/du-lieu", label: "Dữ liệu", icon: BarChart3 },
]

/**
 * The site logo linking home: icon, name and tagline.
 * @param props.shouldHideNameOnPhone - show only the icon below 640px (the name stays for screen
 *   readers); the header needs the room for its three tabs.
 * @returns the logo link.
 */
export function Logo({ shouldHideNameOnPhone = false }: { shouldHideNameOnPhone?: boolean }) {
  return (
    <Link href="/" className="flex items-center gap-2 shrink-0">
      <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-primary text-primary-foreground">
        <Building2 className="h-5 w-5" />
      </span>
      <span className={cn("leading-tight", shouldHideNameOnPhone && "sr-only sm:not-sr-only")}>
        <span className="block font-bold tracking-tight text-foreground">BATDONGSAN AI</span>
        <span className="hidden sm:block text-[11px] text-muted-foreground">Tư vấn bất động sản TP.HCM</span>
      </span>
    </Link>
  )
}

export function Header() {
  const pathname = usePathname()
  const { user, logout, loading } = useAuth()
  const hasLlmKey = useLlmSettingsStatus() === "ready"
  const isOnLlmSettings = pathname === LLM_SETTINGS_PATH
  // Return to the current page after saving a key.
  const llmSettingsHref = isOnLlmSettings || !pathname
    ? LLM_SETTINGS_PATH
    : `${LLM_SETTINGS_PATH}?next=${encodeURIComponent(pathname)}`

  return (
    <div className="border-b border-border bg-card/95 backdrop-blur supports-[backdrop-filter]:bg-card/80">
      <div className="container mx-auto px-4 h-16 flex items-center justify-between gap-2 sm:gap-4">
        <Logo shouldHideNameOnPhone />

        <nav className="flex items-center gap-1 rounded-full bg-muted p-1">
          {NAV_ITEMS.map(({ href, label, icon: Icon }) => {
            const active = pathname === href
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex items-center gap-1.5 rounded-full px-3 py-1.5 text-sm font-medium transition-colors",
                  active
                    ? "bg-card text-primary shadow-sm"
                    : "text-muted-foreground hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4" aria-hidden />
                {/* Labels from 1024px: three labelled tabs do not fit beside the logo and the account buttons below that. */}
                <span className="sr-only lg:not-sr-only">{label}</span>
              </Link>
            )
          })}
        </nav>

        <div className="flex items-center gap-2 shrink-0">
          <Button asChild variant="ghost" size="icon-sm">
            <Link href="/dang-tin" aria-label="Đăng tin" title="Đăng tin" aria-current={pathname === "/dang-tin" ? "page" : undefined}>
              <SquarePlus className="h-4 w-4" aria-hidden />
            </Link>
          </Button>
          <Button asChild variant="ghost" size="icon-sm" className="relative">
            <Link
              href={llmSettingsHref}
              aria-label={hasLlmKey ? "Cài đặt AI, đã lưu API key" : "Cài đặt AI"}
              title="Cài đặt AI"
              aria-current={isOnLlmSettings ? "page" : undefined}
            >
              <KeyRound className="h-4 w-4" aria-hidden />
              {hasLlmKey && (
                <span aria-hidden className="absolute right-1 top-1 h-2 w-2 rounded-full bg-emerald-500 ring-2 ring-card" />
              )}
            </Link>
          </Button>
          {loading ? null : user ? (
            <>
              <span className="hidden md:flex items-center gap-2 text-sm text-foreground">
                <span className="flex h-8 w-8 items-center justify-center rounded-full bg-secondary text-xs font-semibold uppercase text-secondary-foreground">
                  {(user.display_name || user.username).slice(0, 1)}
                </span>
                <span className="max-w-32 truncate font-medium">{user.display_name || user.username}</span>
              </span>
              <Button variant="ghost" size="sm" onClick={logout} aria-label="Đăng xuất">
                <LogOut className="h-4 w-4" />
                <span className="hidden lg:inline">Đăng xuất</span>
              </Button>
            </>
          ) : (
            <>
              <Button asChild variant="ghost" size="sm" className="hidden sm:inline-flex">
                <Link href="/dang-ky">Đăng ký</Link>
              </Button>
              <Button asChild size="sm">
                <Link href="/dang-nhap" aria-label="Đăng nhập">
                  <LogIn className="h-4 w-4 sm:hidden" />
                  <span className="hidden sm:inline">Đăng nhập</span>
                </Link>
              </Button>
            </>
          )}
        </div>
      </div>
    </div>
  )
}
