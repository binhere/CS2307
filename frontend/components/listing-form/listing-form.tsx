"use client"

// The listing form: one listing at a time. Required fields have their own inputs, optional fields
// are typed as one JSON object; photos are shrunk in the browser to a gallery size and a card
// thumbnail before upload; the location is picked on an OpenStreetMap map, which also fills the
// ward and former district from the official boundaries. The server converts,
// checks and stores everything: the form asks it to check whenever the user leaves a field, and
// shows each error, warning and rewritten value under the input it is about. The ward and former
// district lists narrow each other to the places that fit together.

import { useEffect, useId, useRef, useState, type ReactNode } from "react"
import Link from "next/link"
import { AlertTriangle, CheckCircle2, ImagePlus, Loader2, Trash2, XCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { authHeaders, getStoredToken, useAuth } from "@/lib/auth-context"
import { HttpError, extractError, fetchJson, isAbortError, type ApiEnvelope } from "@/lib/http"
import { preparePhoto, type PreparedPhoto } from "@/lib/image-resize"
import {
  METADATA_EXAMPLE,
  buildListingDocument,
  coordinatesFromMetadata,
  feedbackByArea,
  formerDistrictOptions,
  scopedFormerDistrict,
  todayInVietnam,
  wardOptions,
  withLocation,
  type AreaFeedback,
  type FormArea,
  type ListingReference,
  type ListingReport,
  type PickedLocation,
  type RequiredFields,
} from "@/lib/listing-submission"
import { cn } from "@/lib/utils"
import { ListingArchiveUpload } from "./listing-archive-upload"
import { LocationPicker } from "./location-picker"

const FIELD_CLASS =
  "w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-2 text-base shadow-xs outline-none " +
  "focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 md:text-sm " +
  "aria-invalid:border-destructive aria-invalid:ring-destructive/20"
const DEFAULT_PHOTO_LIMIT = 20
// Leaving a field triggers a check; a short delay merges a blur that immediately moves into the next field.
const CHECK_DELAY_MS = 250

/** A photo the user picked, while it is being shrunk and after. */
interface PickedPhoto {
  id: string
  name: string
  previewUrl: string
  status: "processing" | "ready" | "failed"
  prepared?: PreparedPhoto
  error?: string
}

/** Outcome of the last posting. */
interface Posted {
  id: number
  report: ListingReport
  /** The user had already posted the same listing; id is that earlier listing. */
  wasPostedBefore?: boolean
}

/** The two ways to post, shown as tabs. */
const POSTING_MODES = [
  { key: "single", label: "Một tin" },
  { key: "archive", label: "Nhiều tin (zip)" },
] as const
type PostingMode = (typeof POSTING_MODES)[number]["key"]

/**
 * The listing page's content: a sign-in prompt for visitors; for signed-in users, tabs between the
 * one-listing form and the zip upload. Both stay mounted, so switching keeps what was typed.
 * @returns the element.
 */
export function ListingForm() {
  const { user, loading } = useAuth()
  const tabsId = useId()
  const [mode, setMode] = useState<PostingMode>("single")
  if (loading) {
    return <div className="flex items-center gap-2 text-sm text-muted-foreground"><Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />Đang tải</div>
  }
  if (!user) {
    return (
      <div className="rounded-xl border border-border bg-card p-6 text-sm">
        <p className="text-foreground">Vui lòng đăng nhập để đăng tin.</p>
        <Button asChild className="mt-4"><Link href="/dang-nhap?next=/dang-tin">Đăng nhập</Link></Button>
      </div>
    )
  }
  /**
   * Arrow keys move between the tabs, as in the WAI-ARIA tabs pattern.
   * @param event - key event on a tab.
   */
  const moveBetweenTabs = (event: React.KeyboardEvent<HTMLButtonElement>) => {
    if (event.key !== "ArrowLeft" && event.key !== "ArrowRight") return
    event.preventDefault()
    const next = POSTING_MODES[(POSTING_MODES.findIndex((item) => item.key === mode) + 1) % POSTING_MODES.length].key
    setMode(next)
    document.getElementById(`${tabsId}-${next}-tab`)?.focus()
  }
  return (
    <div className="space-y-6">
      <div role="tablist" aria-label="Cách đăng tin" className="inline-flex rounded-lg border border-border bg-muted p-1">
        {POSTING_MODES.map((item) => (
          <button key={item.key} type="button" role="tab" id={`${tabsId}-${item.key}-tab`} aria-controls={`${tabsId}-${item.key}`}
            aria-selected={mode === item.key} tabIndex={mode === item.key ? 0 : -1}
            onClick={() => setMode(item.key)} onKeyDown={moveBetweenTabs}
            className={cn("rounded-md px-4 py-1.5 text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
              mode === item.key ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")}>
            {item.label}
          </button>
        ))}
      </div>
      {POSTING_MODES.map((item) => (
        <div key={item.key} role="tabpanel" id={`${tabsId}-${item.key}`} aria-labelledby={`${tabsId}-${item.key}-tab`} hidden={mode !== item.key}>
          {item.key === "single" ? <ListingFormBody /> : <ListingArchiveUpload />}
        </div>
      ))}
    </div>
  )
}

/** What a control needs to point at its hint and feedback and to show it is invalid. */
interface ControlProps {
  id: string
  describedBy?: string
  isInvalid: boolean
}

/**
 * A labelled field: the label, the control, an optional hint, and what the server said about it.
 * @param props.label - visible label.
 * @param props.hint - optional short hint under the control.
 * @param props.feedback - errors, warnings and rewritten values for this field, once it is shown.
 * @param props.children - render function receiving the control's id, describedby ids and invalid flag.
 * @returns the field element.
 */
function Field({ label, hint, feedback, children }: {
  label: string
  hint?: string
  feedback?: AreaFeedback
  children: (control: ControlProps) => ReactNode
}) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  const hasFeedback = Boolean(feedback && (feedback.errors.length || feedback.warnings.length || feedback.conversions.length))
  const feedbackId = hasFeedback ? `${id}-feedback` : undefined
  const describedBy = [hintId, feedbackId].filter(Boolean).join(" ") || undefined
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-sm font-medium text-foreground">{label}</label>
      {children({ id, describedBy, isInvalid: Boolean(feedback?.errors.length) })}
      {hint && <p id={hintId} className="text-xs text-muted-foreground">{hint}</p>}
      <div id={feedbackId} aria-live="polite">{feedback && <FieldFeedback feedback={feedback} />}</div>
    </div>
  )
}

/**
 * The server's feedback for one field: errors, then warnings, then how a typed value was read.
 * @param props.feedback - the field's errors, warnings and conversions.
 * @returns the feedback lines, or nothing.
 */
function FieldFeedback({ feedback }: { feedback: AreaFeedback }) {
  return (
    <ul className="space-y-1 text-xs">
      {feedback.errors.map((problem, index) => (
        <li key={`error-${index}`} className="flex gap-1.5 text-destructive">
          <XCircle className="mt-px h-3.5 w-3.5 shrink-0" aria-hidden />
          {/* Under its own input, "missing required field «Giá» (price_billion_vnd)" only repeats the label. */}
          <span>{problem.code === "MISSING_REQUIRED" ? "Vui lòng nhập thông tin này." : problem.message}
            {problem.suggestion && <span className="block text-muted-foreground">{problem.suggestion}</span>}</span>
        </li>
      ))}
      {feedback.warnings.map((problem, index) => (
        <li key={`warning-${index}`} className="flex gap-1.5 text-foreground">
          <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0 text-amber-500" aria-hidden />
          <span>{problem.message}{problem.suggestion && <span className="block text-muted-foreground">{problem.suggestion}</span>}</span>
        </li>
      ))}
      {feedback.conversions.map((conversion, index) => (
        <li key={`conversion-${index}`} className="flex gap-1.5 text-muted-foreground">
          <CheckCircle2 className="mt-px h-3.5 w-3.5 shrink-0 text-primary" aria-hidden />
          <span>
            {conversion.kind === "field"
              ? <>«{String(conversion.from)}» được hiểu là trường <span className="font-mono">{conversion.field}</span></>
              : conversion.kind === "derived" || conversion.kind === "geocoded"
                ? <>Tự động điền: <span className="text-foreground">«{formatValue(conversion.to)}»</span></>
                : <>Được hiểu là: <span className="text-foreground">«{formatValue(conversion.to)}»</span></>}
            {conversion.note && <span className="block">{conversion.note}</span>}
          </span>
        </li>
      ))}
    </ul>
  )
}

/**
 * The form itself, for a signed-in user.
 * @returns the form element.
 */
function ListingFormBody() {
  const [reference, setReference] = useState<ListingReference | null>(null)
  const [fields, setFields] = useState<RequiredFields>({
    title: "", description: "", property_type: "", price_billion_vnd: "", area_m2: "",
    ward: "", former_district: "", posted_date: todayInVietnam(),
  })
  const [metadataText, setMetadataText] = useState("")
  const [photos, setPhotos] = useState<PickedPhoto[]>([])
  const [report, setReport] = useState<ListingReport | null>(null)
  const [posted, setPosted] = useState<Posted | null>(null)
  const [formError, setFormError] = useState<string | null>(null)
  const [busy, setBusy] = useState<"checking" | "posting" | null>(null)
  const [locationStatus, setLocationStatus] = useState<string | undefined>(undefined)
  const [touched, setTouched] = useState<ReadonlySet<FormArea>>(new Set())
  const [isEveryFieldShown, setIsEveryFieldShown] = useState(false)
  const [metadataError, setMetadataError] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const photosRef = useRef(photos)
  photosRef.current = photos
  // Checks read the latest input through refs: a delayed check must not send what was typed before.
  const fieldsRef = useRef(fields)
  fieldsRef.current = fields
  const metadataRef = useRef(metadataText)
  metadataRef.current = metadataText
  const checkTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  const checkController = useRef<AbortController | null>(null)
  const locateController = useRef<AbortController | null>(null)
  const lastLocatedPoint = useRef<string | null>(null)
  const photoLimit = reference?.limits.photos ?? DEFAULT_PHOTO_LIMIT
  const feedback = feedbackByArea(report)
  const shownFeedback = (area: FormArea) => (isEveryFieldShown || touched.has(area) ? feedback[area] : undefined)

  useEffect(() => {
    const controller = new AbortController()
    fetchJson<ApiEnvelope<ListingReference>>("/api/listings/reference", { signal: controller.signal })
      .then((envelope) => setReference(envelope.data ?? null))
      .catch((error) => {
        if (!isAbortError(error)) setFormError("Chưa tải được dữ liệu của biểu mẫu. Vui lòng tải lại trang.")
      })
    return () => controller.abort()
  }, [])

  // Preview URLs hold the picked files in memory until revoked.
  useEffect(() => () => photosRef.current.forEach((photo) => URL.revokeObjectURL(photo.previewUrl)), [])

  useEffect(() => () => {
    if (checkTimer.current) clearTimeout(checkTimer.current)
    checkController.current?.abort()
  }, [])

  const setField = (name: keyof RequiredFields) => (value: string) => setFields((current) => ({ ...current, [name]: value }))

  /**
   * Ask the server to check the whole listing as it is now, cancelling a check still under way.
   * An unreadable JSON box is reported under it, and the rest is checked without it.
   * @returns the report, or null when the check failed or was replaced by a newer one.
   */
  const runCheck = async (): Promise<ListingReport | null> => {
    checkController.current?.abort()
    const controller = new AbortController()
    checkController.current = controller
    let built = buildListingDocument(fieldsRef.current, metadataRef.current)
    if ("error" in built) {
      setMetadataError(built.error)
      built = buildListingDocument(fieldsRef.current, "")
      if ("error" in built) return null
    } else {
      setMetadataError(null)
    }
    try {
      const envelope = await fetchJson<ApiEnvelope<{ results: ListingReport[] }>>("/api/listings/validate", {
        method: "POST", headers: authHeaders(), body: JSON.stringify({ listings: [built.document] }), signal: controller.signal,
      }, "Chưa kiểm tra được tin.")
      const checked = envelope.data?.results[0] ?? null
      setReport(checked)
      return checked
    } catch (error) {
      if (!isAbortError(error)) setFormError(error instanceof Error ? error.message : "Chưa kiểm tra được tin.")
      return null
    }
  }

  /**
   * The user left a field (or picked a value): show its feedback from now on and check soon.
   * @param area - the field, or "metadata" for the JSON box.
   */
  const leaveField = (area: FormArea) => leaveFields([area])

  /**
   * Set the ward, and keep the former district only if it fits the new ward (fill it when the ward
   * came from a single former district).
   * @param ward - the ward as typed or picked.
   */
  const setWard = (ward: string) => {
    setFields((current) => ({
      ...current,
      ward,
      former_district: reference ? scopedFormerDistrict(reference, ward, current.former_district) : current.former_district,
    }))
  }

  /**
   * Add picked files, up to the limit, and shrink each one.
   * @param files - the files from the file input.
   */
  const addPhotos = (files: FileList | null) => {
    if (!files) return
    const room = photoLimit - photosRef.current.length
    const picked = Array.from(files).slice(0, Math.max(0, room)).map((file) => ({
      id: `${file.name}-${file.size}-${crypto.randomUUID()}`,
      name: file.name,
      previewUrl: URL.createObjectURL(file),
      status: "processing" as const,
      file,
    }))
    if (files.length > room) setFormError(`Tối đa ${photoLimit} ảnh; đã bỏ qua ${files.length - Math.max(0, room)} ảnh.`)
    setPhotos((current) => [...current, ...picked.map(({ file: _file, ...photo }) => photo)])
    for (const photo of picked) {
      preparePhoto(photo.file)
        .then((prepared) => setPhotos((current) => current.map((item) => item.id === photo.id ? { ...item, status: "ready", prepared } : item)))
        .catch((error: unknown) => setPhotos((current) => current.map((item) => item.id === photo.id
          ? { ...item, status: "failed", error: error instanceof Error ? error.message : "Không xử lý được ảnh." } : item)))
    }
  }

  const removePhoto = (id: string) => {
    setPhotos((current) => {
      const removed = current.find((photo) => photo.id === id)
      if (removed) URL.revokeObjectURL(removed.previewUrl)
      return current.filter((photo) => photo.id !== id)
    })
  }

  /**
   * Mark fields as visited (their feedback shows from now on) and check soon.
   * @param areas - the fields, or "metadata" for the JSON box.
   */
  const leaveFields = (areas: FormArea[]) => {
    setTouched((current) => new Set([...current, ...areas]))
    if (checkTimer.current) clearTimeout(checkTimer.current)
    checkTimer.current = setTimeout(() => void runCheck(), CHECK_DELAY_MS)
  }

  /**
   * A point was picked on the map: write it (and its street) into the JSON box, and set the ward and
   * former district from the official boundaries when the point is new.
   * @param location - the point and, when known, its street.
   */
  const pickLocation = async (location: PickedLocation) => {
    const updated = withLocation(metadataRef.current, location)
    if (updated === null) {
      setMetadataError("Thông tin thêm không phải JSON hợp lệ nên chưa ghi được vị trí.")
      setTouched((current) => new Set(current).add("metadata"))
      return
    }
    metadataRef.current = updated
    setMetadataText(updated)
    // A map click reports the point, then the same point again with its street: look it up once.
    const pointKey = `${location.latitude.toFixed(7)},${location.longitude.toFixed(7)}`
    if (lastLocatedPoint.current !== pointKey) {
      lastLocatedPoint.current = pointKey
      locateController.current?.abort()
      const controller = new AbortController()
      locateController.current = controller
      try {
        const query = new URLSearchParams({ latitude: String(location.latitude), longitude: String(location.longitude) })
        const envelope = await fetchJson<ApiEnvelope<{ is_in_city: boolean; ward: string | null }>>(
          `/api/listings/locate?${query}`, { signal: controller.signal }, "Chưa xác định được phường.")
        const ward = envelope.data?.ward
        if (!envelope.data?.is_in_city || !ward) {
          setLocationStatus("Vị trí nằm ngoài TP.HCM")
        } else {
          setLocationStatus(ward)
          const nextFields = {
            ...fieldsRef.current,
            ward,
            former_district: reference ? scopedFormerDistrict(reference, ward, fieldsRef.current.former_district) : fieldsRef.current.former_district,
          }
          fieldsRef.current = nextFields
          setFields(nextFields)
        }
      } catch (error) {
        if (isAbortError(error)) return
        setLocationStatus(undefined)
      }
    }
    leaveFields(["ward", "former_district", "metadata"])
  }

  /** The user removed the point: drop the coordinates from the JSON box. */
  const clearLocation = () => {
    const updated = withLocation(metadataRef.current, null)
    if (updated === null) return
    metadataRef.current = updated
    setMetadataText(updated)
    lastLocatedPoint.current = null
    setLocationStatus(undefined)
    leaveFields(["metadata"])
  }

  /**
   * Build the document, or show why it cannot be built.
   * @returns the document, or null.
   */
  const documentOrNull = () => {
    const built = buildListingDocument(fields, metadataText)
    if ("error" in built) {
      setFormError(built.error)
      return null
    }
    return built.document
  }

  /** Check the listing without saving it, and show every field's feedback. */
  const check = async () => {
    setFormError(null)
    setPosted(null)
    setIsEveryFieldShown(true)
    if (checkTimer.current) clearTimeout(checkTimer.current)
    setBusy("checking")
    await runCheck()
    setBusy(null)
  }

  /** Post the listing with its photos. */
  const post = async () => {
    setFormError(null)
    setPosted(null)
    setIsEveryFieldShown(true)
    const document = documentOrNull()
    if (!document) return
    const ready = photos.filter((photo) => photo.status === "ready" && photo.prepared)
    if (photos.some((photo) => photo.status === "processing")) {
      setFormError("Vui lòng chờ xử lý xong ảnh.")
      return
    }
    if (ready.length === 0) {
      setFormError("Vui lòng thêm ít nhất 1 ảnh.")
      return
    }
    const body = new FormData()
    body.append("listing", JSON.stringify(document))
    ready.forEach((photo, index) => {
      body.append("photos", photo.prepared!.gallery, `photo-${index + 1}.jpg`)
      body.append("thumbnails", photo.prepared!.thumbnail, `photo-${index + 1}.jpg`)
    })
    const token = getStoredToken()
    setBusy("posting")
    try {
      const envelope = await fetchJson<ApiEnvelope<{ id: number; report: ListingReport }>>("/api/listings", {
        method: "POST", headers: token ? { Authorization: `Bearer ${token}` } : {}, body,
      }, "Chưa đăng được tin.")
      if (envelope.data) {
        setPosted({ id: envelope.data.id, report: envelope.data.report })
        setReport(envelope.data.report)
      }
    } catch (error) {
      const data = error instanceof HttpError
        ? (error.payload as ApiEnvelope<{ id?: number; report?: ListingReport }> | null)?.data
        : undefined
      if (data?.report) setReport(data.report)
      if (error instanceof HttpError && error.status === 409 && typeof data?.id === "number" && data.report) {
        setPosted({ id: data.id, report: data.report, wasPostedBefore: true })
        return
      }
      setFormError(error instanceof HttpError ? extractError(error.payload, error.message) : "Chưa đăng được tin.")
    } finally {
      setBusy(null)
    }
  }

  const processing = photos.filter((photo) => photo.status === "processing").length

  return (
    <form className="space-y-8" onSubmit={(event) => { event.preventDefault(); void post() }} noValidate>
      <section className="space-y-4 rounded-xl border border-border bg-card p-4 sm:p-6" aria-labelledby="listing-main">
        <h2 id="listing-main" className="text-base font-semibold text-foreground">Thông tin chính</h2>
        <Field label="Tiêu đề" feedback={shownFeedback("title")}>
          {({ id, describedBy, isInvalid }) => <Input id={id} aria-describedby={describedBy} aria-invalid={isInvalid || undefined}
            value={fields.title} maxLength={300} onChange={(event) => setField("title")(event.target.value)} onBlur={() => leaveField("title")} />}
        </Field>
        <Field label="Mô tả" feedback={shownFeedback("description")}>
          {({ id, describedBy, isInvalid }) => (
            <textarea id={id} aria-describedby={describedBy} aria-invalid={isInvalid || undefined} rows={6} value={fields.description}
              className={FIELD_CLASS} onChange={(event) => setField("description")(event.target.value)} onBlur={() => leaveField("description")} />
          )}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Loại bất động sản" feedback={shownFeedback("property_type")}>
            {({ id, describedBy, isInvalid }) => (
              <select id={id} aria-describedby={describedBy} aria-invalid={isInvalid || undefined} value={fields.property_type}
                className={cn(FIELD_CLASS, "h-9 py-1")}
                onChange={(event) => { setField("property_type")(event.target.value); leaveField("property_type") }}>
                <option value="">Chọn loại</option>
                {reference?.property_types.map((option) => <option key={option.code} value={option.code}>{option.label}</option>)}
              </select>
            )}
          </Field>
          <Field label="Ngày đăng" feedback={shownFeedback("posted_date")}>
            {({ id, describedBy, isInvalid }) => <Input id={id} type="date" aria-describedby={describedBy} aria-invalid={isInvalid || undefined}
              value={fields.posted_date} max={todayInVietnam()}
              onChange={(event) => setField("posted_date")(event.target.value)} onBlur={() => leaveField("posted_date")} />}
          </Field>
          <Field label="Giá bán" hint="Ví dụ: 4,5 tỷ · 4 tỷ 500 triệu · 850 triệu" feedback={shownFeedback("price_billion_vnd")}>
            {({ id, describedBy, isInvalid }) => <Input id={id} aria-describedby={describedBy} aria-invalid={isInvalid || undefined}
              inputMode="decimal" value={fields.price_billion_vnd}
              onChange={(event) => setField("price_billion_vnd")(event.target.value)} onBlur={() => leaveField("price_billion_vnd")} />}
          </Field>
          <Field label="Diện tích" hint="Ví dụ: 70 m² · 4x15" feedback={shownFeedback("area_m2")}>
            {({ id, describedBy, isInvalid }) => <Input id={id} aria-describedby={describedBy} aria-invalid={isInvalid || undefined}
              value={fields.area_m2}
              onChange={(event) => setField("area_m2")(event.target.value)} onBlur={() => leaveField("area_m2")} />}
          </Field>
          <Field label="Phường/xã" hint="Theo địa giới hành chính từ 1/7/2025; tên phường cũ sẽ được tự chuyển sang tên mới."
            feedback={shownFeedback("ward")}>
            {({ id, describedBy, isInvalid }) => (
              <>
                <Input id={id} aria-describedby={describedBy} aria-invalid={isInvalid || undefined} list={`${id}-options`}
                  value={fields.ward} autoComplete="off"
                  onChange={(event) => setWard(event.target.value)} onBlur={() => leaveField("ward")} />
                <datalist id={`${id}-options`}>
                  {reference && wardOptions(reference, fields.former_district).map((name) => <option key={name} value={name} />)}
                </datalist>
              </>
            )}
          </Field>
          <Field label="Quận/huyện cũ" hint="Tự động điền khi phường chỉ thuộc một quận/huyện cũ." feedback={shownFeedback("former_district")}>
            {({ id, describedBy, isInvalid }) => (
              <>
                <Input id={id} aria-describedby={describedBy} aria-invalid={isInvalid || undefined} list={`${id}-options`}
                  value={fields.former_district} autoComplete="off"
                  onChange={(event) => setField("former_district")(event.target.value)} onBlur={() => leaveField("former_district")} />
                <datalist id={`${id}-options`}>
                  {reference && formerDistrictOptions(reference, fields.ward).map((name) => <option key={name} value={name} />)}
                </datalist>
              </>
            )}
          </Field>
        </div>
      </section>

      <section className="space-y-4 rounded-xl border border-border bg-card p-4 sm:p-6" aria-labelledby="listing-location">
        <h2 id="listing-location" className="text-base font-semibold text-foreground">Vị trí</h2>
        <LocationPicker value={coordinatesFromMetadata(metadataText)} status={locationStatus}
          onPick={(location) => void pickLocation(location)} onClear={clearLocation} />
      </section>

      <section className="space-y-4 rounded-xl border border-border bg-card p-4 sm:p-6" aria-labelledby="listing-photos">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h2 id="listing-photos" className="text-base font-semibold text-foreground">Ảnh</h2>
            <p className="text-xs text-muted-foreground">{photos.length}/{photoLimit} ảnh · ảnh được thu nhỏ trên máy của bạn trước khi tải lên</p>
          </div>
          <input ref={fileInput} type="file" accept="image/*" multiple className="sr-only" id="listing-photo-input"
            onChange={(event) => { addPhotos(event.target.files); event.target.value = "" }} />
          <Button type="button" variant="outline" onClick={() => fileInput.current?.click()} disabled={photos.length >= photoLimit}>
            <ImagePlus className="h-4 w-4" aria-hidden />Chọn ảnh
          </Button>
        </div>
        {photos.length > 0 && (
          <ul className="grid grid-cols-2 gap-3 sm:grid-cols-4 lg:grid-cols-5">
            {photos.map((photo, index) => (
              <li key={photo.id} className="relative overflow-hidden rounded-lg border border-border bg-muted">
                {/* A blob preview of the user's own file; next/image cannot optimise it. */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={photo.previewUrl} alt={`Ảnh ${index + 1}`} className="aspect-[4/3] w-full object-cover" />
                <div className="flex items-center justify-between gap-1 px-2 py-1 text-xs">
                  <span className={cn("truncate", photo.status === "failed" ? "text-destructive" : "text-muted-foreground")}>
                    {photo.status === "processing" ? "Đang xử lý" : photo.status === "failed" ? photo.error : `Ảnh ${index + 1}`}
                  </span>
                  <button type="button" aria-label={`Xóa ảnh ${index + 1}`} onClick={() => removePhoto(photo.id)}
                    className="rounded p-1 text-muted-foreground hover:text-destructive focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring">
                    <Trash2 className="h-3.5 w-3.5" aria-hidden />
                  </button>
                </div>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="space-y-4 rounded-xl border border-border bg-card p-4 sm:p-6" aria-labelledby="listing-metadata">
        <h2 id="listing-metadata" className="text-base font-semibold text-foreground">Thông tin thêm (JSON)</h2>
        <Field label="Các trường tùy chọn" hint="bedroom_count, bathroom_count, legal_status, furnishing, latitude, longitude, street, project, nearby_amenities"
          feedback={metadataFeedback(shownFeedback("metadata"), metadataError, isEveryFieldShown || touched.has("metadata"))}>
          {({ id, describedBy, isInvalid }) => (
            <textarea id={id} aria-describedby={describedBy} aria-invalid={isInvalid || undefined} rows={10} spellCheck={false}
              value={metadataText} placeholder={METADATA_EXAMPLE} className={cn(FIELD_CLASS, "font-mono text-xs md:text-xs")}
              onChange={(event) => setMetadataText(event.target.value)} onBlur={() => leaveField("metadata")} />
          )}
        </Field>
        {reference && (
          <details className="text-xs text-muted-foreground">
            <summary className="cursor-pointer text-foreground">Giá trị hợp lệ của legal_status và furnishing</summary>
            <dl className="mt-2 grid gap-x-4 gap-y-1 sm:grid-cols-[auto_1fr]">
              {[...reference.legal_statuses, ...reference.furnishings].map((option) => (
                <div key={option.code} className="contents">
                  <dt className="font-mono">{option.code}</dt>
                  <dd>{option.label}</dd>
                </div>
              ))}
            </dl>
          </details>
        )}
      </section>

      {formError && <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">{formError}</p>}

      <div className="flex flex-wrap gap-3">
        <Button type="button" variant="outline" onClick={() => void check()} disabled={busy !== null}>
          {busy === "checking" && <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />}Kiểm tra
        </Button>
        <Button type="submit" disabled={busy !== null || processing > 0}>
          {busy === "posting" && <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />}Đăng tin
        </Button>
      </div>

      {posted && (
        <div role="status" className="flex flex-wrap items-center gap-3 rounded-xl border border-border bg-card p-4 text-sm">
          <CheckCircle2 className="h-5 w-5 text-primary" aria-hidden />
          <span className="text-foreground">{posted.wasPostedBefore ? "Bạn đã đăng tin này trước đó." : "Đã đăng tin."}</span>
          <Link href={`/chi-tiet/${posted.id}`} className="font-medium text-primary hover:underline">Xem tin</Link>
        </div>
      )}
      {report && isEveryFieldShown && <ListingReportView report={report} />}
    </form>
  )
}

/**
 * The outcome of an explicit check or post; each field shows its own details.
 * @param props.report - the report of one listing.
 * @returns the summary element.
 */
function ListingReportView({ report }: { report: ListingReport }) {
  return (
    <p role="status" className="flex items-center gap-2 rounded-xl border border-border bg-card p-4 text-sm text-foreground">
      {report.is_valid
        ? <><CheckCircle2 className="h-5 w-5 text-primary" aria-hidden />Tin hợp lệ.</>
        : <><XCircle className="h-5 w-5 text-destructive" aria-hidden />Tin chưa hợp lệ: còn {report.errors.length} lỗi, xem ở từng ô phía trên.</>}
    </p>
  )
}

/**
 * Feedback for the JSON box: the server's, plus a local error when the text is not JSON at all.
 * @param feedback - the server's feedback for the metadata area, if shown.
 * @param metadataError - the local parse error, if any.
 * @param isShown - whether the box's feedback is shown yet.
 * @returns the combined feedback, or undefined when there is nothing to show.
 */
function metadataFeedback(feedback: AreaFeedback | undefined, metadataError: string | null, isShown: boolean): AreaFeedback | undefined {
  if (!isShown) return undefined
  if (!metadataError) return feedback
  const parseError = { field: "metadata", label: "Thông tin thêm", value: null, code: "INVALID_JSON", message: metadataError }
  return { errors: [parseError, ...(feedback?.errors ?? [])], warnings: feedback?.warnings ?? [], conversions: feedback?.conversions ?? [] }
}

/**
 * A reported value as Vietnamese readers write it: numbers with a decimal comma (4,5), text as is.
 * @param value - a value from the report.
 * @returns the display text.
 */
function formatValue(value: unknown): string {
  return typeof value === "number" ? value.toLocaleString("vi-VN", { maximumFractionDigits: 7, useGrouping: false }) : String(value)
}
