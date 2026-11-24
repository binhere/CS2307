"use client"

import { useEffect, useRef, useState } from "react"
import { useRouter } from "next/navigation"
import { ExternalLink, Eye, EyeOff, Loader2 } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select"
import { fetchJson, HttpError, isAbortError, readEnvelopeDetail, type ApiEnvelope } from "@/lib/http"
import {
  CUSTOM_PROVIDER_ID,
  clearLlmSettings,
  llmHeaders,
  readLlmSettings,
  saveLlmSettings,
  useLlmSettings,
  type LlmSettings,
} from "@/lib/llm-settings"
import { cn } from "@/lib/utils"

/** One provider as listed by GET /api/llm/providers. */
interface LlmProvider {
  id: string
  label: string
  default_model?: string | null
  key_url?: string | null
}

/** `data` of GET /api/llm/providers. */
interface ProviderCatalog {
  providers?: LlmProvider[]
  allow_custom_base_url?: boolean
}

/** `data` of a successful POST /api/llm/check. */
interface KeyCheckResult {
  provider?: string
  model?: string
  supports_tools?: boolean
}

/** Result line under the form, describing what the last check, save or clear did. */
interface FormNotice {
  tone: "success" | "error" | "info"
  text: string
}

/** Form state for a first-time user: nothing chosen, not remembered on this device. */
const EMPTY_FORM: LlmSettings = { provider: "", apiKey: "", model: "", baseUrl: "", remember: false }

/** Shown when a key check fails without a backend message (network or proxy failure). */
const CHECK_FAILED_MESSAGE = "Không thể kiểm tra key. Vui lòng thử lại sau."

// Header values must be visible ASCII: a pasted zero-width space or a Vietnamese character
// would make fetch throw before the request is sent, with an unreadable browser error.
const HEADER_SAFE_PATTERN = /^[\x21-\x7E]*$/

/**
 * Accept a `?next=` target only when it stays on this site, so the settings page cannot be
 * used to bounce users to another origin after saving.
 * @param value - raw `next` query value, e.g. "/tim-kiem-chuyen-sau".
 * @returns the same-origin path (with query and hash), or null when absent or unsafe.
 */
function getSafeNextPath(value: string | null): string | null {
  if (!value || !value.startsWith("/") || value.startsWith("//") || value.startsWith("/\\")) return null
  try {
    const target = new URL(value, window.location.origin)
    if (target.origin !== window.location.origin) return null
    return `${target.pathname}${target.search}${target.hash}`
  } catch {
    return null
  }
}

/**
 * Check that a string is an absolute http(s) URL, so links and base URLs from user input or
 * the backend can never be `javascript:` or other schemes.
 * @param value - candidate URL.
 * @returns true for a parseable http: or https: URL.
 */
function isHttpUrl(value: string | null | undefined): value is string {
  if (!value) return false
  try {
    const protocol = new URL(value).protocol
    return protocol === "http:" || protocol === "https:"
  } catch {
    return false
  }
}

/**
 * Trim the form into the settings that are checked and saved; the base URL is dropped for
 * every provider but the custom one, where it is the only place it is used.
 * @param form - current form state.
 * @param isCustomProvider - true when the custom provider is selected and allowed.
 * @returns normalised LlmSettings.
 */
function toLlmSettings(form: LlmSettings, isCustomProvider: boolean): LlmSettings {
  return {
    provider: form.provider.trim(),
    apiKey: form.apiKey.trim(),
    model: form.model.trim(),
    baseUrl: isCustomProvider ? form.baseUrl.trim() : "",
    remember: form.remember,
  }
}

/**
 * Validate the form before a check or save.
 * @param settings - normalised settings from toLlmSettings.
 * @param isCustomProvider - true when the custom provider is selected and allowed.
 * @returns a Vietnamese message describing the first problem, or null when valid.
 */
function validateLlmSettings(settings: LlmSettings, isCustomProvider: boolean): string | null {
  if (!settings.provider || !settings.apiKey) return "Hãy chọn nhà cung cấp và nhập API key."
  if (!HEADER_SAFE_PATTERN.test(settings.apiKey)) return "API key có ký tự không hợp lệ."
  if (!HEADER_SAFE_PATTERN.test(settings.model)) return "Tên model có ký tự không hợp lệ."
  if (isCustomProvider && (!isHttpUrl(settings.baseUrl) || !HEADER_SAFE_PATTERN.test(settings.baseUrl))) {
    return "Base URL không hợp lệ."
  }
  return null
}

/**
 * Form on the "Cài đặt AI" page: choose a provider, enter an API key, optionally a model and
 * (for the custom provider) a base URL, test the key against the backend, and save it in
 * this browser. After saving it follows a same-origin `?next=` path when one is given.
 * @returns the settings form element.
 */
export function LlmSettingsForm() {
  const router = useRouter()
  const savedSettings = useLlmSettings()
  const [form, setForm] = useState<LlmSettings>(EMPTY_FORM)
  const [providers, setProviders] = useState<LlmProvider[]>([])
  const [isCustomBaseUrlAllowed, setIsCustomBaseUrlAllowed] = useState(false)
  const [isProvidersLoading, setIsProvidersLoading] = useState(true)
  const [providersError, setProvidersError] = useState<string | null>(null)
  const [providersRetryCount, setProvidersRetryCount] = useState(0)
  const [isKeyVisible, setIsKeyVisible] = useState(false)
  const [isChecking, setIsChecking] = useState(false)
  const [notice, setNotice] = useState<FormNotice | null>(null)
  // Id of the latest key check; an edit or a newer check bumps it so a late result is ignored.
  const checkRequestRef = useRef(0)
  const checkControllerRef = useRef<AbortController | null>(null)

  const isCustomProvider = form.provider === CUSTOM_PROVIDER_ID && isCustomBaseUrlAllowed
  const selectedProvider = providers.find((provider) => provider.id === form.provider)
  const keyUrl = isHttpUrl(selectedProvider?.key_url) ? selectedProvider?.key_url : null

  // Start from what is saved; Web Storage is only readable after mount.
  useEffect(() => {
    const storedSettings = readLlmSettings()
    if (storedSettings) setForm(storedSettings)
  }, [])

  useEffect(() => {
    const controller = new AbortController()
    setIsProvidersLoading(true)
    setProvidersError(null)
    fetchJson<ApiEnvelope<ProviderCatalog>>("/api/llm/providers", { signal: controller.signal })
      .then((payload) => {
        const catalog = payload?.data
        const isCustomAllowed = catalog?.allow_custom_base_url === true
        // The backend lists "custom" only when it is allowed; filtering again keeps a stale
        // or misconfigured list from offering a base URL the backend would reject.
        const providerList = (catalog?.providers ?? []).filter(
          (provider) => provider.id !== CUSTOM_PROVIDER_ID || isCustomAllowed,
        )
        setProviders(providerList)
        setIsCustomBaseUrlAllowed(isCustomAllowed)
        // A first-time user starts on the first provider instead of an empty select.
        setForm((current) =>
          current.provider || providerList.length === 0 ? current : { ...current, provider: providerList[0].id },
        )
      })
      .catch((error: unknown) => {
        if (isAbortError(error)) return
        setProvidersError("Không tải được danh sách nhà cung cấp.")
      })
      .finally(() => {
        if (!controller.signal.aborted) setIsProvidersLoading(false)
      })
    return () => controller.abort()
  }, [providersRetryCount])

  // A check still running when the page is left has nobody to report to.
  useEffect(() => () => checkControllerRef.current?.abort(), [])

  // Any edit makes the previous check result (and any check in flight) stale.
  const invalidateCheck = () => {
    checkRequestRef.current += 1
    checkControllerRef.current?.abort()
    setIsChecking(false)
    setNotice(null)
  }

  const updateCredentials = (changes: Partial<LlmSettings>) => {
    setForm((current) => ({ ...current, ...changes }))
    invalidateCheck()
  }

  const handleCheck = async () => {
    const settings = toLlmSettings(form, isCustomProvider)
    const validationMessage = validateLlmSettings(settings, isCustomProvider)
    if (validationMessage) {
      setNotice({ tone: "error", text: validationMessage })
      return
    }

    invalidateCheck()
    const requestId = checkRequestRef.current
    const controller = new AbortController()
    checkControllerRef.current = controller
    setIsChecking(true)
    try {
      // Built from the unsaved form so a key can be tested before it is stored.
      const payload = await fetchJson<ApiEnvelope<KeyCheckResult>>("/api/llm/check", {
        method: "POST",
        headers: { "Content-Type": "application/json", ...llmHeaders(settings) },
        body: "{}",
        signal: controller.signal,
      })
      if (requestId !== checkRequestRef.current) return
      const model = payload?.data?.model
      setNotice({ tone: "success", text: model ? `Key hợp lệ với model ${model}.` : "Key hợp lệ." })
    } catch (error) {
      if (isAbortError(error) || requestId !== checkRequestRef.current) return
      const backendDetail = error instanceof HttpError ? readEnvelopeDetail(error.payload) : null
      setNotice({ tone: "error", text: backendDetail ?? CHECK_FAILED_MESSAGE })
    } finally {
      if (requestId === checkRequestRef.current) setIsChecking(false)
    }
  }

  const handleSave = (event: React.FormEvent) => {
    event.preventDefault()
    const settings = toLlmSettings(form, isCustomProvider)
    const validationMessage = validateLlmSettings(settings, isCustomProvider)
    if (validationMessage) {
      setNotice({ tone: "error", text: validationMessage })
      return
    }
    if (!saveLlmSettings(settings)) {
      setNotice({ tone: "error", text: "Trình duyệt không cho phép lưu key." })
      return
    }
    const nextPath = getSafeNextPath(new URLSearchParams(window.location.search).get("next"))
    if (nextPath) {
      router.push(nextPath)
      return
    }
    setForm(settings)
    setNotice({ tone: "success", text: "Đã lưu." })
  }

  const handleClear = () => {
    clearLlmSettings()
    invalidateCheck()
    setForm((current) => ({ ...current, apiKey: "" }))
    setNotice({ tone: "info", text: "Đã xóa key khỏi trình duyệt này." })
  }

  return (
    <form onSubmit={handleSave} noValidate className="space-y-5 rounded-xl border border-border bg-card p-5 shadow-sm md:p-6">
      <div className="space-y-1.5">
        <Label htmlFor="llm-provider">Nhà cung cấp</Label>
        {providersError ? (
          <div className="flex items-center justify-between gap-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">
            <span>{providersError}</span>
            <Button type="button" variant="outline" size="sm" onClick={() => setProvidersRetryCount((count) => count + 1)}>
              Thử lại
            </Button>
          </div>
        ) : (
          <Select
            value={form.provider}
            onValueChange={(value) => updateCredentials({ provider: value, model: "" })}
            disabled={isProvidersLoading}
          >
            <SelectTrigger id="llm-provider" className="w-full bg-background data-[size=default]:h-11">
              <SelectValue placeholder={isProvidersLoading ? "Đang tải…" : "Chọn nhà cung cấp"} />
            </SelectTrigger>
            <SelectContent>
              {providers.map((provider) => (
                <SelectItem key={provider.id} value={provider.id}>
                  {provider.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        )}
      </div>

      <div className="space-y-1.5">
        <div className="flex items-center justify-between gap-2">
          <Label htmlFor="llm-api-key">API key</Label>
          {keyUrl && (
            <a
              href={keyUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-sm font-medium text-primary hover:underline"
            >
              Lấy API key
              <ExternalLink className="h-3.5 w-3.5" aria-hidden />
            </a>
          )}
        </div>
        <div className="relative">
          <Input
            id="llm-api-key"
            name="llm-api-key"
            type={isKeyVisible ? "text" : "password"}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            // Keeps password managers from offering to save the key as a site login.
            data-1p-ignore
            data-lpignore="true"
            value={form.apiKey}
            onChange={(event) => updateCredentials({ apiKey: event.target.value })}
            className="h-11 pr-11 font-mono"
          />
          <button
            type="button"
            aria-label="Hiện API key"
            aria-pressed={isKeyVisible}
            onClick={() => setIsKeyVisible((isVisible) => !isVisible)}
            className="absolute right-1 top-1/2 -translate-y-1/2 rounded-md p-2 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-[3px] focus-visible:ring-ring/50"
          >
            {isKeyVisible ? <EyeOff className="h-4 w-4" aria-hidden /> : <Eye className="h-4 w-4" aria-hidden />}
          </button>
        </div>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="llm-model">
          Model <span className="font-normal text-muted-foreground">(tùy chọn)</span>
        </Label>
        <Input
          id="llm-model"
          name="llm-model"
          autoComplete="off"
          autoCapitalize="none"
          spellCheck={false}
          placeholder={selectedProvider?.default_model || "Mặc định của nhà cung cấp"}
          value={form.model}
          onChange={(event) => updateCredentials({ model: event.target.value })}
          className="h-11 font-mono"
        />
      </div>

      {isCustomProvider && (
        <div className="space-y-1.5">
          <Label htmlFor="llm-base-url">Base URL</Label>
          <Input
            id="llm-base-url"
            name="llm-base-url"
            type="url"
            inputMode="url"
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            placeholder="https://api.example.com/v1"
            value={form.baseUrl}
            onChange={(event) => updateCredentials({ baseUrl: event.target.value })}
            className="h-11 font-mono"
          />
        </div>
      )}

      <div className="flex items-start gap-2.5">
        <Checkbox
          id="llm-remember"
          checked={form.remember}
          onCheckedChange={(checked) => setForm((current) => ({ ...current, remember: checked === true }))}
          className="mt-0.5"
        />
        <div>
          <Label htmlFor="llm-remember">Nhớ trên thiết bị này</Label>
          <p className="mt-1 text-xs text-muted-foreground">Nếu không chọn, key sẽ bị xóa khi bạn đóng tab.</p>
        </div>
      </div>

      <p
        aria-live="polite"
        className={cn(
          "min-h-5 text-sm",
          notice?.tone === "success" && "text-emerald-600 dark:text-emerald-400",
          notice?.tone === "error" && "text-destructive",
          notice?.tone === "info" && "text-muted-foreground",
        )}
      >
        {notice?.text}
      </p>

      <div className="flex flex-wrap gap-2">
        <Button type="button" variant="outline" className="h-11" onClick={() => void handleCheck()} disabled={isChecking}>
          {isChecking ? (
            <>
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden />
              Đang kiểm tra…
            </>
          ) : (
            "Kiểm tra"
          )}
        </Button>
        <Button type="submit" className="h-11 flex-1">
          Lưu
        </Button>
        <Button
          type="button"
          variant="ghost"
          className="h-11 text-destructive hover:bg-destructive/10 hover:text-destructive"
          onClick={handleClear}
          disabled={!savedSettings && !form.apiKey}
        >
          Xóa key
        </Button>
      </div>
    </form>
  )
}
