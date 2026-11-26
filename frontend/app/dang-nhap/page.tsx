"use client"

import { useState } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { useAuth } from "@/lib/auth-context"
import { AuthShell } from "@/components/auth-shell"
import { DEMO_ACCOUNT } from "@/lib/demo-account"
import { safeRedirectPath } from "@/lib/listing-submission"

export default function LoginPage() {
  const { login } = useAuth()
  const router = useRouter()
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState<"form" | "demo" | null>(null)

  /**
   * Sign in and go where the visitor was headed.
   * @param account - username and password.
   * @param source - which button is waiting, for its spinner.
   */
  const signIn = async (account: { username: string; password: string }, source: "form" | "demo") => {
    setError(null)
    setLoading(source)
    try {
      await login(account.username, account.password)
      // Pages that need an account send visitors here with ?next=<their path>.
      const next = safeRedirectPath(new URLSearchParams(window.location.search).get("next"))
      router.push(next ?? "/tim-kiem-chuyen-sau")
    } catch (err) {
      setError(err instanceof Error ? err.message : "Đăng nhập thất bại")
    } finally {
      setLoading(null)
    }
  }

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault()
    void signIn({ username: username.trim(), password }, "form")
  }

  return (
    <AuthShell
      title="Đăng nhập"
      subtitle="Chào mừng bạn quay lại."
      footer={<>Chưa có tài khoản? <Link href="/dang-ky" className="font-medium text-primary hover:underline">Đăng ký</Link></>}
    >
      <form onSubmit={handleSubmit} className="space-y-4">
        <label className="block">
          <span className="text-sm font-medium text-foreground">Tên đăng nhập</span>
          <Input
            name="username"
            autoComplete="username"
            autoCapitalize="none"
            spellCheck={false}
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            className="mt-1.5 h-11"
            autoFocus
            required
          />
        </label>
        <label className="block">
          <span className="text-sm font-medium text-foreground">Mật khẩu</span>
          <Input
            type="password"
            name="password"
            autoComplete="current-password"
            autoCapitalize="none"
            spellCheck={false}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            className="mt-1.5 h-11"
            required
          />
        </label>
        {error && <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>}
        <Button type="submit" disabled={loading !== null || !username.trim() || !password} className="h-11 w-full">
          {loading === "form" ? <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" /> : "Đăng nhập"}
        </Button>
      </form>
      <div className="mt-6 space-y-2 border-t border-border pt-6">
        <Button type="button" variant="outline" disabled={loading !== null} className="h-11 w-full"
          onClick={() => void signIn(DEMO_ACCOUNT, "demo")}>
          {loading === "demo" ? <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" /> : "Dùng tài khoản demo"}
        </Button>
        <p className="text-center text-xs text-muted-foreground">
          {DEMO_ACCOUNT.username} · mật khẩu {DEMO_ACCOUNT.password}
          <span className="block">Tài khoản dùng chung: ai cũng thấy lịch sử chat và tin đã lưu của nó.</span>
        </p>
      </div>
    </AuthShell>
  )
}
