// Bring-your-own-key LLM settings. The key never leaves the browser except as request
// headers on AI calls, so it is kept in Web Storage: localStorage when the user asks to be
// remembered on this device, sessionStorage (gone when the tab closes) otherwise.
import { useSyncExternalStore } from "react"

/** LLM credentials and options the user entered on the "Cài đặt AI" page. */
export interface LlmSettings {
  /** Provider id from GET /api/llm/providers, e.g. "gemini" or "custom". */
  provider: string
  apiKey: string
  /** Model name; "" means the provider's default model. */
  model: string
  /** OpenAI-compatible endpoint; only used when `provider` is "custom". */
  baseUrl: string
  /** True keeps the settings in localStorage across browser restarts. */
  remember: boolean
}

/** Whether usable settings exist: unknown until the client has hydrated, since the server cannot read Web Storage. */
export type LlmSettingsStatus = "unknown" | "missing" | "ready"

/** Web Storage key holding the JSON-encoded LlmSettings. */
export const LLM_SETTINGS_STORAGE_KEY = "cs2307.llmSettings"

/** Provider id whose requests may carry a user-supplied base URL. */
export const CUSTOM_PROVIDER_ID = "custom"

/** Window event fired after this tab saves or clears the settings; other tabs learn via `storage`. */
const LLM_SETTINGS_CHANGE_EVENT = "cs2307:llm-settings-change"

// useSyncExternalStore needs the same object back while nothing changed, so the parsed
// settings are cached against the raw stored string they came from.
let cachedRawSettings: string | null = null
let cachedSettings: LlmSettings | null = null

/**
 * Read one Web Storage area without throwing (storage can be blocked or unavailable).
 * @param area - "local" or "session".
 * @returns the raw stored string, or null when absent or unreadable.
 */
function readStorageItem(area: "local" | "session"): string | null {
  try {
    const storage = area === "local" ? window.localStorage : window.sessionStorage
    return storage.getItem(LLM_SETTINGS_STORAGE_KEY)
  } catch {
    return null
  }
}

/**
 * Write or remove the settings in one Web Storage area without throwing.
 * @param area - "local" or "session".
 * @param value - string to store, or null to remove the item.
 * @returns true when the operation succeeded.
 */
function writeStorageItem(area: "local" | "session", value: string | null): boolean {
  try {
    const storage = area === "local" ? window.localStorage : window.sessionStorage
    if (value === null) storage.removeItem(LLM_SETTINGS_STORAGE_KEY)
    else storage.setItem(LLM_SETTINGS_STORAGE_KEY, value)
    return true
  } catch {
    return false
  }
}

/**
 * Parse stored JSON into LlmSettings, tolerating missing optional fields.
 * @param raw - stored string, possibly malformed or from an older version.
 * @returns the settings, or null when the string is not a usable settings object.
 */
function parseLlmSettings(raw: string | null): LlmSettings | null {
  if (!raw) return null
  try {
    const value = JSON.parse(raw) as Partial<Record<keyof LlmSettings, unknown>> | null
    if (!value || typeof value !== "object") return null
    const readText = (field: unknown) => (typeof field === "string" ? field : "")
    return {
      provider: readText(value.provider),
      apiKey: readText(value.apiKey),
      model: readText(value.model),
      baseUrl: readText(value.baseUrl),
      remember: value.remember === true,
    }
  } catch {
    return null
  }
}

/**
 * Tell subscribers in this tab that the settings changed.
 */
function notifyLlmSettingsChange(): void {
  try {
    window.dispatchEvent(new Event(LLM_SETTINGS_CHANGE_EVENT))
  } catch {
    // Without the event, components pick up the change on their next render.
  }
}

/**
 * Read the saved settings; a remembered (localStorage) copy wins over a session one.
 * @returns the saved settings, or null on the server or when nothing usable is stored.
 */
export function readLlmSettings(): LlmSettings | null {
  if (typeof window === "undefined") return null
  const raw = readStorageItem("local") ?? readStorageItem("session")
  if (raw !== cachedRawSettings) {
    cachedRawSettings = raw
    cachedSettings = parseLlmSettings(raw)
  }
  return cachedSettings
}

/**
 * Save the settings to exactly one store, chosen by `remember`, and remove any copy from
 * the other store so an unticked "remember" really forgets the key on this device.
 * @param settings - settings to store.
 * @returns true when the settings were written.
 */
export function saveLlmSettings(settings: LlmSettings): boolean {
  const targetArea = settings.remember ? "local" : "session"
  const otherArea = settings.remember ? "session" : "local"
  const isSaved = writeStorageItem(targetArea, JSON.stringify(settings))
  if (isSaved) writeStorageItem(otherArea, null)
  notifyLlmSettingsChange()
  return isSaved
}

/**
 * Remove the settings, including the API key, from both stores.
 */
export function clearLlmSettings(): void {
  writeStorageItem("local", null)
  writeStorageItem("session", null)
  notifyLlmSettingsChange()
}

/**
 * Whether settings carry what every AI request needs: a provider and an API key.
 * @param settings - settings to inspect, or null.
 * @returns true when both provider and API key are non-empty.
 */
export function isLlmSettingsComplete(settings: LlmSettings | null): settings is LlmSettings {
  return Boolean(settings && settings.provider.trim() && settings.apiKey.trim())
}

/**
 * Build the X-LLM-* request headers the backend reads for a single AI request.
 * Empty values are omitted, and the base URL is only sent for the custom provider.
 * @param settings - settings to encode; defaults to the saved ones.
 * @returns a header map, empty when no settings exist.
 */
export function llmHeaders(settings: LlmSettings | null = readLlmSettings()): Record<string, string> {
  const headers: Record<string, string> = {}
  if (!settings) return headers
  const provider = settings.provider.trim()
  const apiKey = settings.apiKey.trim()
  const model = settings.model.trim()
  const baseUrl = settings.baseUrl.trim()
  if (provider) headers["X-LLM-Provider"] = provider
  if (apiKey) headers["X-LLM-Api-Key"] = apiKey
  if (model) headers["X-LLM-Model"] = model
  if (baseUrl && provider === CUSTOM_PROVIDER_ID) headers["X-LLM-Base-Url"] = baseUrl
  return headers
}

/**
 * Subscribe to settings changes from this tab (custom event) and other tabs (`storage`).
 * @param onChange - called after any change.
 * @returns a function that removes both listeners.
 */
function subscribeToLlmSettings(onChange: () => void): () => void {
  const handleStorage = (event: StorageEvent) => {
    // A null key means another tab called storage.clear().
    if (event.key === null || event.key === LLM_SETTINGS_STORAGE_KEY) onChange()
  }
  window.addEventListener(LLM_SETTINGS_CHANGE_EVENT, onChange)
  window.addEventListener("storage", handleStorage)
  return () => {
    window.removeEventListener(LLM_SETTINGS_CHANGE_EVENT, onChange)
    window.removeEventListener("storage", handleStorage)
  }
}

/**
 * Server snapshot: Web Storage does not exist during SSR, so the server always renders
 * as if nothing were saved.
 * @returns null.
 */
function getServerLlmSettings(): LlmSettings | null {
  return null
}

/**
 * Current saved settings, re-rendering the caller whenever they change in any tab.
 * @returns the saved settings, or null on the server, during hydration, or when none are saved.
 */
export function useLlmSettings(): LlmSettings | null {
  return useSyncExternalStore(subscribeToLlmSettings, readLlmSettings, getServerLlmSettings)
}

/**
 * Subscription for a value that never changes after hydration.
 * @returns a no-op unsubscribe function.
 */
function subscribeToNothing(): () => void {
  return () => undefined
}

/**
 * Whether saved settings are usable, distinguishing "not known yet" (server render and
 * hydration) from "missing", so pages do not flash a missing-key notice at users who have one.
 * @returns "unknown" before hydration, then "ready" or "missing".
 */
export function useLlmSettingsStatus(): LlmSettingsStatus {
  const settings = useLlmSettings()
  const hasHydrated = useSyncExternalStore(subscribeToNothing, () => true, () => false)
  if (!hasHydrated) return "unknown"
  return isLlmSettingsComplete(settings) ? "ready" : "missing"
}
