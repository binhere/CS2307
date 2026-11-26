"use client"

import { memo, useState, useRef, useEffect, useCallback } from "react"
import Link from "next/link"
import { Send, Loader2, Sparkles, Plus, MessageSquare, ThumbsUp, ThumbsDown, SlidersHorizontal, Lightbulb, Scale, KeyRound, TriangleAlert } from "lucide-react"
import ReactMarkdown, { type Components } from "react-markdown"
import remarkGfm from "remark-gfm"
import { Button } from "@/components/ui/button"
import { PropertyCard } from "@/components/property-card"
import { useAuth, authHeaders } from "@/lib/auth-context"
import { sendInteraction } from "@/lib/feedback"
import { fetchJson, HttpError, readEnvelopeCode, readEnvelopeDetail, type ApiEnvelope } from "@/lib/http"
import { llmHeaders, useLlmSettingsStatus } from "@/lib/llm-settings"
import { cn, getPreferredScrollBehavior } from "@/lib/utils"

interface PropertyResult {
  id: string
  listing_id?: number | string
  explanation?: string
  comparison?: string
  [key: string]: any
}

interface ChatMessage {
  /** Stable identity: the React key, and how a pending reply is replaced in place. */
  id: string
  role: "user" | "ai"
  content?: string
  properties?: PropertyResult[]
  timestamp: string
  // Client-side error notices are UI only and must not be replayed as history.
  isError?: boolean
  /** True for an error the user fixes on the AI settings page; the bubble links there. */
  isLlmSettingsError?: boolean
}

interface ConversationSummary {
  id: number
  title: string | null
}

/** `data` of POST /api/chat: the assistant's answer plus any recommended listings. */
interface ChatReply {
  results?: PropertyResult[]
  assistant_answer?: string
  search_performed?: boolean
  conversation_id?: number | null
  parsed_query?: unknown
}

/** One saved message as returned by GET /api/chat/conversations/:id. */
interface SavedConversationMessage {
  role: string
  content?: string
  results?: PropertyResult[] | null
  created_at?: string
}

/** The user's verdict on one recommended listing. */
type ThumbVote = "up" | "down"

// Mirrors ClientHistoryTurn.content max_length in the backend chat endpoint.
const HISTORY_CONTENT_MAX = 4000

// Chat survives reloads and navigating to a listing and back (per tab).
const STORAGE_PREFIX = "ai-chat:v1:"
const STORED_MESSAGES_MAX = 30

interface StoredChat {
  conversationId?: number | null
  messages?: ChatMessage[]
  guestContextListingIds?: string[]
  guestContextParsedQuery?: any
}

function readStoredChat(key: string): StoredChat | null {
  try {
    const raw = window.sessionStorage.getItem(key)
    return raw ? (JSON.parse(raw) as StoredChat) : null
  } catch {
    return null
  }
}

function writeStoredChat(key: string, value: StoredChat) {
  try {
    window.sessionStorage.setItem(key, JSON.stringify(value))
  } catch {
    // Storage may be full or blocked; the chat still works without persistence.
  }
}

interface AIChatProps {
  filters?: any
  /** Human-readable summary of the applied criteria, shown above the conversation. */
  criteria?: string[]
  onEditCriteria?: () => void
}

const SUGGESTED_PROMPTS = [
  "Căn hộ 2 phòng ngủ dưới 4 tỷ, gần metro",
  "Nhà phố sổ hồng riêng ở Gò Vấp, khoảng 60–80 m²",
  "Căn hộ có hồ bơi, gym, phù hợp gia đình có trẻ nhỏ",
  "Ngân sách 5 tỷ nên mua căn hộ hay nhà đất?",
]

/** Greeting that opens every new chat; also how a stored greeting is recognised and kept out of history. */
const WELCOME_CONTENT =
  "Xin chào! Tôi là trợ lý AI chuyên về bất động sản. Dựa trên các tiêu chí bạn đang nhập, tôi có thể giúp bạn tìm căn hộ hoặc nhà phố phù hợp nhất. Bạn muốn tôi gợi ý Căn hộ/Nhà đất như thế nào?"

/** Id of the greeting message; a chat holds at most one. */
const WELCOME_MESSAGE_ID = "welcome"

/** Bubble text for a failed reply when the backend supplied no message of its own. */
const CHAT_ERROR_MESSAGE = "Xin lỗi, đã xảy ra lỗi khi xử lý tin nhắn. Vui lòng thử lại sau."

/** AI settings page, returning to this chat after the key is saved. */
const LLM_SETTINGS_HREF = "/cai-dat-ai?next=/tim-kiem-chuyen-sau"

/**
 * Backend error codes caused by the user's own LLM settings (missing or rejected key,
 * unknown provider, a model without tool calling), so the fix is on the settings page.
 */
const LLM_SETTINGS_ERROR_CODES = new Set(["LLM_KEY_REQUIRED", "LLM_KEY_INVALID", "LLM_PROVIDER_INVALID", "LLM_MODEL_NO_TOOLS"])

/** Content held by a reply still being generated; the bubble shows a typing indicator instead. */
const PENDING_REPLY_CONTENT = "Đang xử lý…"

/** Remark plugins for chat answers, hoisted so ReactMarkdown receives the same array on every render. */
const REMARK_PLUGINS = [remarkGfm]

/** Vietnamese 24-hour "HH:mm" formatter, built once because Intl formatters are costly to create. */
const TIME_FORMATTER = new Intl.DateTimeFormat("vi-VN", { hour: "2-digit", minute: "2-digit" })

// Ids only need to be unique within this tab; a counter avoids crypto.randomUUID, which is
// missing on plain-HTTP origins such as a LAN address.
let messageSequence = 0

/**
 * Create a chat message id that is unique within this browser tab.
 * @returns a string such as "message-lq3k2x-7".
 */
function createMessageId(): string {
  messageSequence += 1
  return `message-${Date.now().toString(36)}-${messageSequence}`
}

/**
 * Build the greeting message that opens a chat.
 * @param timestamp - display time; "" before mount, so server and client render the same markup.
 * @returns the greeting ChatMessage.
 */
function createWelcomeMessage(timestamp: string): ChatMessage {
  return { id: WELCOME_MESSAGE_ID, role: "ai", content: WELCOME_CONTENT, timestamp }
}

/**
 * Current local time for a live message.
 * @returns "HH:mm" in the browser's time zone.
 */
function nowTime(): string {
  return TIME_FORMATTER.format(new Date())
}

/**
 * Display time of a saved message.
 * @param value - ISO timestamp from the backend, or undefined.
 * @returns "HH:mm" for a valid timestamp, otherwise the current time.
 */
function formatTime(value?: string): string {
  if (!value) return nowTime()
  const date = new Date(value)
  return isNaN(date.getTime()) ? nowTime() : TIME_FORMATTER.format(date)
}

/**
 * Give messages stored by an older build (which had no ids) an id, keeping existing ones.
 * @param messages - messages read from sessionStorage.
 * @returns the same messages, each with an `id`.
 */
function withMessageIds(messages: ChatMessage[]): ChatMessage[] {
  return messages.map((message) => (message.id ? message : { ...message, id: createMessageId() }))
}

/** What a failed reply shows: its bubble text and whether to link to the AI settings page. */
interface ChatErrorDescription {
  content: string
  isLlmSettingsError: boolean
}

/**
 * Turn a failed chat request into what its bubble shows.
 * @param error - value thrown by fetchJson or fetch.
 * @returns the message the backend wrote into its error envelope (Vietnamese, meant for end
 *   users) or CHAT_ERROR_MESSAGE for proxy, network and plain-text failures whose text is
 *   technical, plus whether the error code points at the user's LLM settings.
 */
function describeChatError(error: unknown): ChatErrorDescription {
  if (!(error instanceof HttpError)) return { content: CHAT_ERROR_MESSAGE, isLlmSettingsError: false }
  const errorCode = readEnvelopeCode(error.payload)
  return {
    content: readEnvelopeDetail(error.payload) ?? CHAT_ERROR_MESSAGE,
    isLlmSettingsError: errorCode !== null && LLM_SETTINGS_ERROR_CODES.has(errorCode),
  }
}

// `node` (the hast element react-markdown passes to every component) is pulled out of the
// props so it is not spread onto DOM elements as an invalid attribute.
const markdownComponents: Components = {
  a: ({ node: _node, children, ...props }) => (
    <a
      {...props}
      target="_blank"
      rel="noopener noreferrer"
      className="font-medium text-primary underline underline-offset-2 hover:opacity-80"
    >
      {children}
    </a>
  ),
  blockquote: ({ node: _node, children, ...props }) => (
    <blockquote {...props} className="border-l-2 border-primary/50 pl-3 italic text-muted-foreground">
      {children}
    </blockquote>
  ),
  code: ({ node: _node, children, className, ...props }) => {
    const isBlock = className?.startsWith("language-")
    return (
      <code
        {...props}
        className={
          isBlock
            ? `${className} block min-w-max text-xs`
            : "rounded bg-background/80 px-1 py-0.5 font-mono text-[0.85em]"
        }
      >
        {children}
      </code>
    )
  },
  pre: ({ node: _node, children, ...props }) => (
    <pre {...props} className="overflow-x-auto rounded-lg bg-background p-3 font-mono text-xs">
      {children}
    </pre>
  ),
  table: ({ node: _node, children, ...props }) => (
    <div className="overflow-x-auto">
      <table {...props} className="w-full border-collapse text-left text-xs">
        {children}
      </table>
    </div>
  ),
  th: ({ node: _node, children, ...props }) => (
    <th {...props} className="border border-border bg-background/70 px-2 py-1.5 font-semibold">
      {children}
    </th>
  ),
  td: ({ node: _node, children, ...props }) => (
    <td {...props} className="border border-border px-2 py-1.5 align-top">
      {children}
    </td>
  ),
}

/**
 * Render one assistant answer as GitHub-flavoured markdown. Memoised because parsing is the
 * most expensive part of a chat render and an answer's text never changes once shown.
 * @param props.content - markdown source of the answer.
 * @returns the rendered answer.
 */
const MarkdownMessage = memo(function MarkdownMessage({ content }: { content: string }) {
  return (
    <div className="text-sm leading-relaxed [&>*:first-child]:mt-0 [&>*:last-child]:mb-0 [&_h1]:my-3 [&_h1]:text-lg [&_h1]:font-bold [&_h2]:my-3 [&_h2]:text-base [&_h2]:font-bold [&_h3]:my-2 [&_h3]:font-semibold [&_hr]:my-3 [&_hr]:border-border [&_li]:my-1 [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5 [&_p]:my-2 [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5">
      <ReactMarkdown remarkPlugins={REMARK_PLUGINS} components={markdownComponents}>
        {content}
      </ReactMarkdown>
    </div>
  )
})

interface ChatMessageRowProps {
  message: ChatMessage
  /** True while this message is the placeholder for a reply still being generated. */
  isPending: boolean
  /** The user's verdicts so far, keyed by listing id. */
  thumbs: Record<string, ThumbVote>
  onThumb: (property: PropertyResult, isHelpful: boolean) => void
}

/**
 * One chat bubble with its recommended listings and feedback buttons. Memoised so that a new
 * message, or a reply arriving, does not re-render every earlier row.
 * @param props - see ChatMessageRowProps.
 * @returns the row element.
 */
const ChatMessageRow = memo(function ChatMessageRow({ message, isPending, thumbs, onThumb }: ChatMessageRowProps) {
  return (
    <div className={`flex gap-3 ${message.role === "user" ? "justify-end" : "justify-start"}`}>
      {message.role === "ai" && (
        <div className={cn("mt-0.5 w-8 h-8 rounded-full flex items-center justify-center flex-shrink-0",
          message.isError ? "bg-destructive text-white" : "bg-secondary")}>
          {message.isError
            ? <TriangleAlert className="w-4 h-4" aria-hidden />
            : <Sparkles className="w-4 h-4 text-primary" aria-hidden />}
        </div>
      )}

      <div className={cn("min-w-0", message.role === "user" ? "max-w-[85%] sm:max-w-[75%]" : "flex-1")}>
        <div
          role={message.isError ? "alert" : undefined}
          className={cn(
            "rounded-2xl px-4 py-3",
            message.role === "user"
              ? "ml-auto w-fit bg-primary text-primary-foreground rounded-tr-sm"
              : "w-fit max-w-full bg-card border border-border text-foreground rounded-tl-sm",
            // Errors stand out: a failing model or provider must be noticed and fixed, not read past.
            message.isError && "border-2 border-destructive bg-destructive/10 text-destructive [&_*]:text-destructive",
          )}
        >
          {isPending ? (
            <span className="flex items-center gap-2 py-0.5 text-sm text-muted-foreground">
              <span className="flex gap-1">
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-primary [animation-delay:-0.3s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-primary [animation-delay:-0.15s]" />
                <span className="h-1.5 w-1.5 animate-bounce rounded-full bg-primary" />
              </span>
              Đang tìm và phân tích…
            </span>
          ) : message.role === "ai" ? (
            <>
              <MarkdownMessage content={message.content || ""} />
              {message.isLlmSettingsError && (
                <Button asChild variant="outline" size="sm" className="mt-3">
                  <Link href={LLM_SETTINGS_HREF}>
                    <KeyRound className="h-4 w-4" aria-hidden />
                    Cài đặt AI
                  </Link>
                </Button>
              )}
            </>
          ) : (
            <p className="whitespace-pre-wrap text-sm leading-relaxed">{message.content}</p>
          )}
        </div>

        {/* Recommended properties */}
        {message.properties && message.properties.length > 0 && (
          <div className="mt-3 space-y-4">
            {message.properties.slice(0, 3).map((property, rank) => {
              const thumb = thumbs[String(property.listing_id ?? property.id)]
              return (
                <div key={property.id} className="overflow-hidden rounded-xl border border-border bg-card shadow-sm">
                  <div className="flex items-center justify-between gap-2 border-b border-border px-4 py-2">
                    <span className="text-xs font-semibold text-primary">Gợi ý #{rank + 1}</span>
                    {/* Explicit feedback on this recommendation */}
                    <div className="flex items-center gap-1">
                      <span className="mr-1 hidden text-xs text-muted-foreground sm:inline">
                        {thumb ? "Cảm ơn phản hồi của bạn" : "Gợi ý này có phù hợp?"}
                      </span>
                      <button
                        type="button"
                        aria-label="Hữu ích"
                        aria-pressed={thumb === "up"}
                        className={cn("rounded-md p-1.5 transition-colors hover:bg-muted", thumb === "up" ? "text-primary" : "text-muted-foreground")}
                        onClick={() => onThumb(property, true)}
                      >
                        <ThumbsUp className={cn("h-4 w-4", thumb === "up" && "fill-current")} />
                      </button>
                      <button
                        type="button"
                        aria-label="Không phù hợp"
                        aria-pressed={thumb === "down"}
                        className={cn("rounded-md p-1.5 transition-colors hover:bg-muted", thumb === "down" ? "text-destructive" : "text-muted-foreground")}
                        onClick={() => onThumb(property, false)}
                      >
                        <ThumbsDown className={cn("h-4 w-4", thumb === "down" && "fill-current")} />
                      </button>
                    </div>
                  </div>

                  <div className="p-2 [&_article]:border-0 [&_article]:hover:translate-y-0 [&_article]:hover:shadow-none">
                    <PropertyCard property={property as any} source="chat" />
                  </div>

                  {(property.explanation || property.comparison) && (
                    <div className="space-y-3 border-t border-border bg-accent/40 px-4 py-3 text-sm">
                      {property.explanation && (
                        <div className="flex gap-2.5">
                          <Lightbulb className="mt-0.5 h-4 w-4 flex-shrink-0 text-primary" />
                          <div>
                            <p className="font-semibold text-foreground">Lý do đề xuất</p>
                            <p className="mt-0.5 text-muted-foreground">{property.explanation}</p>
                          </div>
                        </div>
                      )}
                      {property.comparison && (
                        <div className="flex gap-2.5">
                          <Scale className="mt-0.5 h-4 w-4 flex-shrink-0 text-primary" />
                          <div>
                            <p className="font-semibold text-foreground">Đánh giá</p>
                            <p className="mt-0.5 text-muted-foreground">{property.comparison}</p>
                          </div>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}

        {!isPending && (
          <p className={`text-[10px] mt-1 text-muted-foreground ${message.role === "user" ? "text-right" : ""}`}>
            {message.timestamp}
          </p>
        )}
      </div>
    </div>
  )
})

interface ChatComposerProps {
  /** True when chatting is impossible (no LLM key yet): the whole composer is disabled. */
  isDisabled: boolean
  /** True while a reply or a stored conversation is loading: typing stays allowed, sending does not. */
  isSendBlocked: boolean
  /** True while a reply is being generated; swaps the send icon for a spinner. */
  isWaitingForReply: boolean
  /** Receives the raw input; returns true when the message was accepted and the input may be cleared. */
  onSend: (text: string) => boolean
}

/**
 * Message input with its own text state, so typing re-renders only this form and not the
 * conversation above it. The textarea stays enabled while a reply loads so it keeps focus.
 * @param props - see ChatComposerProps.
 * @returns the composer area element.
 */
function ChatComposer({ isDisabled, isSendBlocked, isWaitingForReply, onSend }: ChatComposerProps) {
  const [inputValue, setInputValue] = useState("")

  const submitInput = () => {
    if (onSend(inputValue)) setInputValue("")
  }

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault()
    submitInput()
  }

  const handleInputKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    // Enter sends, Shift+Enter inserts a newline; ignore Enter while composing Vietnamese IME input.
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault()
      submitInput()
    }
  }

  return (
    <div className="shrink-0 border-t border-border bg-card px-4 pt-3 pb-2">
      <form
        onSubmit={handleSubmit}
        className="mx-auto flex max-w-3xl items-end gap-2 rounded-2xl border border-input bg-background p-1.5 pl-4 focus-within:border-primary focus-within:ring-2 focus-within:ring-primary/20"
      >
        <textarea
          rows={1}
          aria-label="Tin nhắn cho trợ lý"
          placeholder="Ví dụ: căn hộ 2PN ở Thủ Đức dưới 4 tỷ, gần trường học…"
          className="max-h-36 min-h-9 flex-1 resize-none bg-transparent py-2 text-sm outline-none [field-sizing:content] placeholder:text-muted-foreground disabled:cursor-not-allowed disabled:opacity-50"
          value={inputValue}
          onChange={(event) => setInputValue(event.target.value)}
          onKeyDown={handleInputKeyDown}
          disabled={isDisabled}
        />
        <Button
          type="submit"
          size="icon"
          className="h-9 w-9 rounded-xl flex-shrink-0"
          disabled={isDisabled || isSendBlocked || !inputValue.trim()}
          aria-label="Gửi"
        >
          {isWaitingForReply ? <Loader2 className="h-4 w-4 animate-spin" /> : <Send className="h-4 w-4" />}
        </Button>
      </form>
      <p className="mx-auto mt-1.5 max-w-3xl text-center text-[11px] text-muted-foreground">
        Trợ lý AI có thể sai sót. Hãy kiểm tra pháp lý và thông tin thực tế trước khi giao dịch.
      </p>
    </div>
  )
}

export function AIChat({ filters: propFilters = {}, criteria = [], onEditCriteria }: AIChatProps) {
  const { user, loading: authLoading } = useAuth()
  const [messages, setMessages] = useState<ChatMessage[]>(() => [createWelcomeMessage("")])
  // Id of the placeholder awaiting a reply; null when no reply is in flight.
  const [pendingMessageId, setPendingMessageId] = useState<string | null>(null)
  const [isConversationLoading, setIsConversationLoading] = useState(false)
  const [conversationId, setConversationId] = useState<number | null>(null)
  const [conversations, setConversations] = useState<ConversationSummary[]>([])
  const [guestContextListingIds, setGuestContextListingIds] = useState<string[]>([])
  const [guestContextParsedQuery, setGuestContextParsedQuery] = useState<any>(null)
  const currentFilters = propFilters
  // Logged-in and guest chats are stored separately so switching account never leaks one into the other.
  const identity = user ? `user:${user.id}` : "guest"
  const storageKey = STORAGE_PREFIX + identity
  const [hydratedIdentity, setHydratedIdentity] = useState<string | null>(null)
  const messagesContainerRef = useRef<HTMLDivElement>(null)
  // Bumped whenever the visible thread is replaced; replies and conversation loads that
  // started under an older version are dropped instead of landing in the wrong thread.
  const threadVersionRef = useRef(0)
  // Per-listing thumbs state so the user sees their feedback was recorded.
  const [thumbs, setThumbs] = useState<Record<string, ThumbVote>>({})
  const isLoading = pendingMessageId !== null
  // "unknown" until hydration: the composer stays disabled but no missing-key notice flashes.
  const llmSettingsStatus = useLlmSettingsStatus()
  const isLlmReady = llmSettingsStatus === "ready"

  // The greeting's time is filled in after mount: rendering it during SSR would bake the
  // build or server time into the HTML and break hydration.
  useEffect(() => {
    setMessages((previous) =>
      previous.map((message) =>
        message.id === WELCOME_MESSAGE_ID && !message.timestamp ? { ...message, timestamp: nowTime() } : message,
      ),
    )
  }, [])

  const loadConversations = useCallback(async () => {
    if (!user) return
    try {
      const payload = await fetchJson<ApiEnvelope<ConversationSummary[]>>("/api/chat/conversations", {
        headers: authHeaders(),
      })
      setConversations(payload?.data || [])
    } catch {
      // The history strip is optional; the chat works without it.
    }
  }, [user])

  // Load the conversation list when the user logs in.
  useEffect(() => {
    if (user) loadConversations()
    else {
      setConversations([])
      setConversationId(null)
      setGuestContextListingIds([])
      setGuestContextParsedQuery(null)
    }
  }, [user, loadConversations])

  // Auto scroll to bottom
  useEffect(() => {
    // Scroll only the message list; scrollIntoView would also shift the page's overflow-hidden ancestors.
    const container = messagesContainerRef.current
    container?.scrollTo({ top: container.scrollHeight, behavior: getPreferredScrollBehavior() })
  }, [messages])

  // Replace the visible thread with a fresh greeting and drop any reply or load still in flight.
  const resetChat = useCallback(() => {
    threadVersionRef.current += 1
    setPendingMessageId(null)
    setIsConversationLoading(false)
    setConversationId(null)
    setGuestContextListingIds([])
    setGuestContextParsedQuery(null)
    setMessages([createWelcomeMessage(nowTime())])
  }, [])

  const openConversation = useCallback(async (id: number) => {
    // A newer click (or a reset) bumps the version, so only the latest load is applied.
    threadVersionRef.current += 1
    const requestVersion = threadVersionRef.current
    setIsConversationLoading(true)
    try {
      const payload = await fetchJson<ApiEnvelope<{ messages?: SavedConversationMessage[] }>>(
        `/api/chat/conversations/${id}`,
        { headers: authHeaders() },
      )
      if (threadVersionRef.current !== requestVersion) return
      const rehydrated = (payload?.data?.messages || []).map(
        (savedMessage): ChatMessage => ({
          id: createMessageId(),
          role: savedMessage.role === "assistant" ? "ai" : "user",
          content: savedMessage.content,
          properties: Array.isArray(savedMessage.results) && savedMessage.results.length > 0 ? savedMessage.results : undefined,
          timestamp: formatTime(savedMessage.created_at),
        }),
      )
      setConversationId(id)
      setMessages(rehydrated.length > 0 ? rehydrated : [createWelcomeMessage(nowTime())])
    } catch {
      // Keep the current thread when a saved conversation cannot be loaded.
    } finally {
      if (threadVersionRef.current === requestVersion) setIsConversationLoading(false)
    }
  }, [])

  // Restore the saved chat once auth is known, and start clean when the account changes.
  useEffect(() => {
    if (authLoading || hydratedIdentity === identity) return
    if (hydratedIdentity !== null) resetChat()
    const saved = readStoredChat(storageKey)
    if (saved) {
      if (user) {
        if (saved.conversationId) openConversation(saved.conversationId)
      } else {
        if (saved.messages?.length) setMessages(withMessageIds(saved.messages))
        setGuestContextListingIds(saved.guestContextListingIds || [])
        setGuestContextParsedQuery(saved.guestContextParsedQuery ?? null)
      }
    }
    setHydratedIdentity(identity)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [authLoading, identity])

  // Persist after each completed turn (never the transient pending placeholder). While a saved
  // conversation is still loading, the stored id is kept so a failed load does not lose it.
  useEffect(() => {
    if (authLoading || hydratedIdentity !== identity || isLoading || isConversationLoading) return
    writeStoredChat(
      storageKey,
      user
        ? { conversationId }
        : {
            messages: messages.slice(-STORED_MESSAGES_MAX),
            guestContextListingIds,
            guestContextParsedQuery,
          },
    )
  }, [authLoading, hydratedIdentity, identity, isLoading, isConversationLoading, user, storageKey, conversationId, messages, guestContextListingIds, guestContextParsedQuery])

  const startNewChat = () => {
    resetChat()
  }

  const applyReply = (reply: ChatReply | null | undefined, replyMessageId: string) => {
    const properties: PropertyResult[] = reply?.results || []
    const assistantAnswer = reply?.assistant_answer
    const isSearchPerformed = Boolean(reply?.search_performed)
    setMessages((previous) =>
      previous.map((message): ChatMessage =>
        message.id === replyMessageId
          ? {
              id: replyMessageId,
              role: "ai",
              content:
                assistantAnswer || "Tôi chưa thể trả lời ngay lúc này. Bạn thử diễn đạt lại giúp tôi nhé.",
              properties: isSearchPerformed && properties.length > 0 ? properties : undefined,
              timestamp: nowTime(),
            }
          : message,
      ),
    )
    if (reply?.conversation_id) setConversationId(reply.conversation_id)
    // Guest context is a list of server-issued IDs only. The backend rehydrates
    // those IDs rather than trusting listing fields sent from the browser.
    if (!user && properties.length > 0) {
      setGuestContextListingIds(
        properties
          .map((property) => property.listing_id)
          .filter((id) => id !== undefined && id !== null)
          .slice(0, 20)
          .map(String),
      )
      if (reply?.parsed_query) setGuestContextParsedQuery(reply.parsed_query)
    }
  }

  const requestReply = async (
    userQuestion: string,
    guestHistory: Array<{ role: string; content: string }>,
    replyMessageId: string,
  ) => {
    const requestVersion = threadVersionRef.current
    try {
      const requestBody = user
        ? { message: userQuestion, conversation_id: conversationId, top_k: 3, filters: currentFilters }
        : {
            message: userQuestion,
            top_k: 3,
            filters: currentFilters,
            history: guestHistory,
            context_listing_ids: guestContextListingIds,
            context_parsed_query: guestContextParsedQuery,
          }

      const replyEnvelope = await fetchJson<ApiEnvelope<ChatReply>>("/api/chat", {
        method: "POST",
        // The user's own LLM key travels with this request only; the backend does not store it.
        headers: { ...authHeaders(), ...llmHeaders() },
        body: JSON.stringify(requestBody),
      })
      if (threadVersionRef.current !== requestVersion) return
      applyReply(replyEnvelope?.data, replyMessageId)
      if (user) loadConversations()
    } catch (error) {
      if (threadVersionRef.current !== requestVersion) return
      const errorDescription = describeChatError(error)
      setMessages((previous) =>
        previous.map((message): ChatMessage =>
          message.id === replyMessageId
            ? {
                id: replyMessageId,
                role: "ai",
                content: errorDescription.content,
                timestamp: nowTime(),
                isError: true,
                isLlmSettingsError: errorDescription.isLlmSettingsError,
              }
            : message,
        ),
      )
    } finally {
      setPendingMessageId((current) => (current === replyMessageId ? null : current))
    }
  }

  // Returns whether the message was accepted, so the composer only clears accepted input.
  const sendMessage = (text: string): boolean => {
    if (!text.trim() || isLoading || isConversationLoading || !isLlmReady) return false

    const guestHistory = messages
      .filter((item) => item.content && !item.isError && !(item.role === "ai" && item.content === WELCOME_CONTENT))
      .slice(-20)
      .map((item) => ({
        role: item.role === "ai" ? "assistant" : "user",
        content: item.content!.slice(0, HISTORY_CONTENT_MAX),
      }))
    const replyMessageId = createMessageId()
    setMessages((previous) => [
      ...previous,
      { id: createMessageId(), role: "user", content: text, timestamp: nowTime() },
      { id: replyMessageId, role: "ai", content: PENDING_REPLY_CONTENT, timestamp: nowTime() },
    ])
    setPendingMessageId(replyMessageId)
    void requestReply(text, guestHistory, replyMessageId)
    return true
  }

  const handleThumb = useCallback(
    (property: PropertyResult, isHelpful: boolean) => {
      setThumbs((previous) => ({ ...previous, [String(property.listing_id ?? property.id)]: isHelpful ? "up" : "down" }))
      sendInteraction({
        listing_id: property.listing_id ?? property.id,
        action_type: isHelpful ? "thumbs_up" : "thumbs_down",
        source: "chat",
        conversation_id: conversationId ?? undefined,
      })
    },
    [conversationId],
  )

  const isFreshChat = messages.length === 1 && messages[0].role === "ai" && messages[0].content === WELCOME_CONTENT
  const isSendBlocked = isLoading || isConversationLoading || !isLlmReady

  return (
    <div className="h-full flex flex-col overflow-hidden">
      {/* Chat Header */}
      <div className="shrink-0 flex items-center gap-3 px-4 py-3 bg-card border-b border-border">
        <div className="w-10 h-10 rounded-full bg-primary text-primary-foreground flex items-center justify-center">
          <Sparkles className="w-5 h-5" />
        </div>
        <div className="flex-1 min-w-0">
          <h3 className="font-semibold text-foreground leading-tight">Trợ lý tư vấn bất động sản</h3>
          <p className="text-xs text-muted-foreground truncate">Mô tả nhu cầu, trợ lý sẽ gợi ý và giải thích lý do</p>
        </div>
        {onEditCriteria && (
          <Button type="button" variant="outline" size="sm" onClick={onEditCriteria} className="lg:hidden">
            <SlidersHorizontal className="h-4 w-4" />
            <span className="hidden sm:inline">Tiêu chí</span>
            {criteria.length > 0 && (
              <span className="rounded-full bg-primary px-1.5 text-xs text-primary-foreground">{criteria.length}</span>
            )}
          </Button>
        )}
        {!isFreshChat && (
          <Button type="button" variant="outline" size="sm" onClick={startNewChat} disabled={isLoading} aria-label="Cuộc trò chuyện mới">
            <Plus className="h-4 w-4" />
            <span className="hidden sm:inline">Cuộc trò chuyện mới</span>
          </Button>
        )}
      </div>

      {/* Conversation history (logged-in only) */}
      {user && conversations.length > 0 && (
        <div className="shrink-0 flex gap-2 overflow-x-auto px-4 py-2 border-b border-border bg-card">
          {conversations.map((c) => (
            <button
              key={c.id}
              type="button"
              onClick={() => openConversation(c.id)}
              // A pending reply belongs to the visible thread, so switching
              // conversations mid-request would orphan it.
              disabled={isLoading}
              className={`flex max-w-56 items-center gap-1.5 whitespace-nowrap text-xs px-2.5 py-1.5 rounded-full border transition-colors disabled:opacity-50 disabled:cursor-not-allowed ${
                c.id === conversationId
                  ? "bg-primary text-primary-foreground border-primary"
                  : "bg-background text-foreground border-border hover:bg-muted"
              }`}
            >
              <MessageSquare className="h-3 w-3 shrink-0" />
              <span className="truncate">{c.title || `Cuộc trò chuyện ${c.id}`}</span>
            </button>
          ))}
        </div>
      )}

      {/* Applied criteria */}
      {criteria.length > 0 && (
        <div className="shrink-0 flex items-center gap-2 overflow-x-auto px-4 py-2 border-b border-border bg-accent/60 text-xs">
          <span className="shrink-0 font-medium text-accent-foreground">Tiêu chí:</span>
          {criteria.map((item) => (
            <span key={item} className="shrink-0 rounded-full border border-primary/20 bg-card px-2.5 py-1 text-foreground">
              {item}
            </span>
          ))}
        </div>
      )}

      {/* Chat Messages */}
      <div ref={messagesContainerRef} className="flex-1 overflow-y-auto">
        <div className="mx-auto w-full max-w-3xl space-y-5 px-4 py-6">
          {/* A log region announces new messages and finished replies to screen readers. */}
          <div role="log" aria-live="polite" className="space-y-5">
            {messages.map((message) => (
              <ChatMessageRow
                key={message.id}
                message={message}
                isPending={message.id === pendingMessageId}
                thumbs={thumbs}
                onThumb={handleThumb}
              />
            ))}
          </div>

          {llmSettingsStatus === "missing" && (
            <div className="flex flex-col gap-3 rounded-xl border border-primary/20 bg-accent/60 p-4 sm:flex-row sm:items-center sm:justify-between">
              <div className="flex items-start gap-3">
                <KeyRound className="mt-0.5 h-5 w-5 shrink-0 text-primary" aria-hidden />
                <div>
                  <p className="font-semibold text-foreground">Cần API key để dùng Tư vấn AI</p>
                  <p className="mt-0.5 text-sm text-muted-foreground">Key chỉ lưu trong trình duyệt của bạn.</p>
                </div>
              </div>
              <Button asChild size="sm" className="shrink-0">
                <Link href={LLM_SETTINGS_HREF}>Thêm API key</Link>
              </Button>
            </div>
          )}

          {isFreshChat && (
            <div className="grid gap-2 pl-11 sm:grid-cols-2">
              {SUGGESTED_PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => sendMessage(prompt)}
                  disabled={isSendBlocked}
                  className="rounded-xl border border-border bg-card px-4 py-3 text-left text-sm text-foreground transition-colors hover:border-primary/40 hover:bg-accent disabled:opacity-50"
                >
                  {prompt}
                </button>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Input Area */}
      <ChatComposer
        isDisabled={!isLlmReady}
        isSendBlocked={isSendBlocked}
        isWaitingForReply={isLoading}
        onSend={sendMessage}
      />
    </div>
  )
}
