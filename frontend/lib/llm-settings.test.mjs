// Tests for the bring-your-own-key settings store: a saved API key lives only in the browser's
// Web Storage and is removed 24 hours after it was saved, whichever store holds it.
// Run with: npm test (node --test, TypeScript sources loaded with --experimental-strip-types).
import assert from "node:assert/strict"
import { beforeEach, test } from "node:test"

/** In-memory stand-in for one Web Storage area. */
class MemoryStorage {
  constructor() {
    this.items = new Map()
  }
  getItem(key) {
    return this.items.has(key) ? this.items.get(key) : null
  }
  setItem(key, value) {
    this.items.set(key, String(value))
  }
  removeItem(key) {
    this.items.delete(key)
  }
}

globalThis.window = {
  localStorage: new MemoryStorage(),
  sessionStorage: new MemoryStorage(),
  addEventListener() {},
  removeEventListener() {},
  dispatchEvent() {
    return true
  },
}

const { LLM_SETTINGS_LIFETIME_MS, LLM_SETTINGS_STORAGE_KEY, llmHeaders, readLlmSettings, saveLlmSettings } =
  await import("./llm-settings.ts")

const SAVED_AT = Date.UTC(2026, 10, 26, 7, 0, 0)
const SETTINGS = { provider: "gemini", apiKey: "test-key-123", model: "", baseUrl: "", remember: false }

/** Whether either Web Storage area still holds the settings. */
function isStoredAnywhere() {
  return (
    window.localStorage.getItem(LLM_SETTINGS_STORAGE_KEY) !== null ||
    window.sessionStorage.getItem(LLM_SETTINGS_STORAGE_KEY) !== null
  )
}

beforeEach(() => {
  window.localStorage.removeItem(LLM_SETTINGS_STORAGE_KEY)
  window.sessionStorage.removeItem(LLM_SETTINGS_STORAGE_KEY)
})

test("a saved key is usable until 24 hours after it was saved", () => {
  assert.equal(saveLlmSettings(SETTINGS, SAVED_AT), true)
  const settings = readLlmSettings(SAVED_AT + LLM_SETTINGS_LIFETIME_MS - 1)
  assert.equal(settings?.apiKey, "test-key-123")
  assert.equal(settings?.expiresAt, SAVED_AT + LLM_SETTINGS_LIFETIME_MS)
  assert.equal(llmHeaders(settings)["X-LLM-Api-Key"], "test-key-123")
})

test("after 24 hours the key is removed from the device and no longer sent", () => {
  saveLlmSettings(SETTINGS, SAVED_AT)
  assert.equal(readLlmSettings(SAVED_AT + LLM_SETTINGS_LIFETIME_MS), null)
  assert.equal(isStoredAnywhere(), false)
  assert.deepEqual(llmHeaders(), {})
})

test("a key remembered on this device expires too", () => {
  saveLlmSettings({ ...SETTINGS, remember: true }, SAVED_AT)
  assert.notEqual(window.localStorage.getItem(LLM_SETTINGS_STORAGE_KEY), null)
  assert.equal(readLlmSettings(SAVED_AT + LLM_SETTINGS_LIFETIME_MS + 1), null)
  assert.equal(isStoredAnywhere(), false)
})

test("saving again starts a new 24-hour lifetime", () => {
  saveLlmSettings(SETTINGS, SAVED_AT)
  const savedAgainAt = SAVED_AT + 20 * 60 * 60 * 1000
  saveLlmSettings(SETTINGS, savedAgainAt)
  assert.equal(readLlmSettings(SAVED_AT + LLM_SETTINGS_LIFETIME_MS + 1)?.apiKey, "test-key-123")
  assert.equal(readLlmSettings(savedAgainAt + LLM_SETTINGS_LIFETIME_MS), null)
})

test("a key saved before expiry existed is removed on the next read", () => {
  window.localStorage.setItem(LLM_SETTINGS_STORAGE_KEY, JSON.stringify({ ...SETTINGS, remember: true }))
  assert.equal(readLlmSettings(SAVED_AT), null)
  assert.equal(isStoredAnywhere(), false)
})
