"use client"

// The listing form: one listing at a time. Required fields have their own inputs, optional fields
// are typed as one JSON object; photos are shrunk in the browser to a gallery size and a card
// thumbnail before upload; "Tìm tọa độ" asks OpenStreetMap from the browser. The server converts,
// checks and stores everything, and its report (errors, warnings, rewritten values) is shown as is.

import { useEffect, useId, useRef, useState, type ReactNode } from "react"
import Link from "next/link"
import { AlertTriangle, CheckCircle2, ImagePlus, Loader2, MapPin, Trash2, XCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { authHeaders, getStoredToken, useAuth } from "@/lib/auth-context"
import { geocodeAddress, OSM_ATTRIBUTION } from "@/lib/geocoding"
import { HttpError, extractError, fetchJson, isAbortError, type ApiEnvelope } from "@/lib/http"
import { preparePhoto, type PreparedPhoto } from "@/lib/image-resize"
import {
  METADATA_EXAMPLE,
  addressFromMetadata,
  buildListingDocument,
  todayInVietnam,
  withCoordinates,
  type ListingReference,
  type ListingReport,
  type RequiredFields,
} from "@/lib/listing-submission"
import { cn } from "@/lib/utils"

const FIELD_CLASS =
  "w-full min-w-0 rounded-md border border-input bg-transparent px-3 py-2 text-base shadow-xs outline-none " +
  "focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50 md:text-sm"
const DEFAULT_PHOTO_LIMIT = 20

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
}

/**
 * The listing page's content: a sign-in prompt for visitors, the form for signed-in users.
 * @returns the element.
 */
export function ListingForm() {
  const { user, loading } = useAuth()
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
  return <ListingFormBody />
}

/**
 * A labelled field: the label text, the control and an optional hint tied to it.
 * @param props.label - visible label.
 * @param props.hint - optional short hint under the control.
 * @param props.children - render function receiving the control's id and the hint's id.
 * @returns the field element.
 */
function Field({ label, hint, children }: { label: string; hint?: string; children: (id: string, hintId?: string) => ReactNode }) {
  const id = useId()
  const hintId = hint ? `${id}-hint` : undefined
  return (
    <div className="space-y-1.5">
      <label htmlFor={id} className="text-sm font-medium text-foreground">{label}</label>
      {children(id, hintId)}
      {hint && <p id={hintId} className="text-xs text-muted-foreground">{hint}</p>}
    </div>
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
  const [geocoding, setGeocoding] = useState<{ status: "searching" | "found" | "missing" | "error"; text: string } | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const photosRef = useRef(photos)
  photosRef.current = photos
  const photoLimit = reference?.limits.photos ?? DEFAULT_PHOTO_LIMIT

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

  const setField = (name: keyof RequiredFields) => (value: string) => setFields((current) => ({ ...current, [name]: value }))

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

  /** Find coordinates for the street or project in the optional JSON and write them into it. */
  const findCoordinates = async () => {
    const address = addressFromMetadata(metadataText)
    if (!address.street && !address.project) {
      setGeocoding({ status: "missing", text: "Vui lòng điền \"street\" hoặc \"project\" vào thông tin thêm." })
      return
    }
    setGeocoding({ status: "searching", text: "Đang tìm" })
    // The ward as the server resolved it at the last check ("phuong thanh my loi" -> "Phường Cát Lái")
    // finds more than the typed one; without a match, retry the street alone. The server checks the
    // coordinates against the ward's boundary either way.
    const resolvedWard = typeof report?.listing.ward === "string" ? report.listing.ward : undefined
    const ward = resolvedWard ?? (fields.ward.trim() || undefined)
    try {
      let place = await geocodeAddress({ ...address, ward })
      if (!place && ward) place = await geocodeAddress(address)
      if (!place) {
        setGeocoding({ status: "missing", text: "Không tìm thấy địa chỉ này." })
        return
      }
      const updated = withCoordinates(metadataText, place.latitude, place.longitude)
      if (updated === null) {
        setGeocoding({ status: "error", text: "Thông tin thêm không phải JSON hợp lệ." })
        return
      }
      setMetadataText(updated)
      setGeocoding({ status: "found", text: place.label })
    } catch {
      setGeocoding({ status: "error", text: "Chưa tìm được tọa độ. Vui lòng thử lại sau." })
    }
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

  /** Ask the server to check the listing without saving it. */
  const check = async () => {
    setFormError(null)
    setPosted(null)
    const document = documentOrNull()
    if (!document) return
    setBusy("checking")
    try {
      const envelope = await fetchJson<ApiEnvelope<{ results: ListingReport[] }>>("/api/listings/validate", {
        method: "POST", headers: authHeaders(), body: JSON.stringify({ listings: [document] }),
      }, "Chưa kiểm tra được tin.")
      setReport(envelope.data?.results[0] ?? null)
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "Chưa kiểm tra được tin.")
    } finally {
      setBusy(null)
    }
  }

  /** Post the listing with its photos. */
  const post = async () => {
    setFormError(null)
    setPosted(null)
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
      const refused = error instanceof HttpError
        ? (error.payload as ApiEnvelope<{ report?: ListingReport }> | null)?.data?.report
        : undefined
      if (refused) setReport(refused)
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
        <Field label="Tiêu đề">
          {(id) => <Input id={id} value={fields.title} onChange={(event) => setField("title")(event.target.value)} maxLength={300} />}
        </Field>
        <Field label="Mô tả">
          {(id) => (
            <textarea id={id} rows={6} value={fields.description} className={FIELD_CLASS}
              onChange={(event) => setField("description")(event.target.value)} />
          )}
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="Loại bất động sản">
            {(id) => (
              <select id={id} value={fields.property_type} className={cn(FIELD_CLASS, "h-9 py-1")}
                onChange={(event) => setField("property_type")(event.target.value)}>
                <option value="">Chọn loại</option>
                {reference?.property_types.map((option) => <option key={option.code} value={option.code}>{option.label}</option>)}
              </select>
            )}
          </Field>
          <Field label="Ngày đăng">
            {(id) => <Input id={id} type="date" value={fields.posted_date} max={todayInVietnam()}
              onChange={(event) => setField("posted_date")(event.target.value)} />}
          </Field>
          <Field label="Giá bán" hint="Ví dụ: 4,5 tỷ · 4 tỷ 500 triệu · 850 triệu">
            {(id, hintId) => <Input id={id} aria-describedby={hintId} inputMode="decimal" value={fields.price_billion_vnd}
              onChange={(event) => setField("price_billion_vnd")(event.target.value)} />}
          </Field>
          <Field label="Diện tích" hint="Ví dụ: 70 m² · 4x15">
            {(id, hintId) => <Input id={id} aria-describedby={hintId} value={fields.area_m2}
              onChange={(event) => setField("area_m2")(event.target.value)} />}
          </Field>
          <Field label="Phường/xã" hint="Theo địa giới hành chính từ 1/7/2025; tên phường cũ sẽ được tự chuyển sang tên mới.">
            {(id, hintId) => (
              <>
                <Input id={id} aria-describedby={hintId} list={`${id}-options`} value={fields.ward} autoComplete="off"
                  onChange={(event) => setField("ward")(event.target.value)} />
                <datalist id={`${id}-options`}>{reference?.wards.map((name) => <option key={name} value={name} />)}</datalist>
              </>
            )}
          </Field>
          <Field label="Quận/huyện cũ" hint="Có thể để trống khi phường chỉ thuộc một quận/huyện cũ.">
            {(id, hintId) => (
              <>
                <Input id={id} aria-describedby={hintId} list={`${id}-options`} value={fields.former_district} autoComplete="off"
                  onChange={(event) => setField("former_district")(event.target.value)} />
                <datalist id={`${id}-options`}>{reference?.former_districts.map((name) => <option key={name} value={name} />)}</datalist>
              </>
            )}
          </Field>
        </div>
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
        <Field label="Các trường tùy chọn" hint="bedroom_count, bathroom_count, legal_status, furnishing, latitude, longitude, street, project, nearby_amenities">
          {(id, hintId) => (
            <textarea id={id} aria-describedby={hintId} rows={10} spellCheck={false} value={metadataText}
              placeholder={METADATA_EXAMPLE} className={cn(FIELD_CLASS, "font-mono text-xs md:text-xs")}
              onChange={(event) => setMetadataText(event.target.value)} />
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
        <div className="flex flex-wrap items-center gap-3">
          <Button type="button" variant="outline" onClick={() => void findCoordinates()} disabled={geocoding?.status === "searching"}>
            {geocoding?.status === "searching" ? <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden /> : <MapPin className="h-4 w-4" aria-hidden />}
            Tìm tọa độ
          </Button>
          {geocoding && (
            <p role="status" className={cn("text-xs", geocoding.status === "found" ? "text-foreground" : "text-muted-foreground")}>
              {geocoding.text}
              {geocoding.status === "found" && <span className="ml-2 text-muted-foreground">{OSM_ATTRIBUTION}</span>}
            </p>
          )}
        </div>
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
          <span className="text-foreground">Đã đăng tin.</span>
          <Link href={`/chi-tiet/${posted.id}`} className="font-medium text-primary hover:underline">Xem tin</Link>
        </div>
      )}
      {report && <ListingReportView report={report} />}
    </form>
  )
}

/**
 * The server's report: errors to fix, warnings, and every value it rewrote.
 * @param props.report - the report of one listing.
 * @returns the report element.
 */
function ListingReportView({ report }: { report: ListingReport }) {
  return (
    <section className="space-y-4 rounded-xl border border-border bg-card p-4 sm:p-6" aria-labelledby="listing-report" aria-live="polite">
      <h2 id="listing-report" className="flex items-center gap-2 text-base font-semibold text-foreground">
        {report.is_valid
          ? <><CheckCircle2 className="h-5 w-5 text-primary" aria-hidden />Tin hợp lệ</>
          : <><XCircle className="h-5 w-5 text-destructive" aria-hidden />Tin chưa hợp lệ</>}
      </h2>
      {report.errors.length > 0 && (
        <ProblemList title="Lỗi cần sửa" tone="error" problems={report.errors} />
      )}
      {report.warnings.length > 0 && (
        <ProblemList title="Cảnh báo" tone="warning" problems={report.warnings} />
      )}
      {report.conversions.length > 0 && (
        <div>
          <h3 className="text-sm font-medium text-foreground">Nội dung đã tự chuyển đổi</h3>
          <ul className="mt-2 space-y-1.5 text-sm">
            {report.conversions.map((item, index) => (
              <li key={index} className="text-muted-foreground">
                <span className="font-mono text-xs text-foreground">{item.field}</span>
                {item.kind === "field"
                  ? <> · đổi từ tên trường «{String(item.from)}»</>
                  : <> · {item.from === null ? "trống" : `«${formatValue(item.from)}»`} → <span className="text-foreground">«{formatValue(item.to)}»</span></>}
                {item.note && <span className="block text-xs">{item.note}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
    </section>
  )
}

/**
 * A reported value as Vietnamese readers write it: numbers with a decimal comma (4,5), text as is.
 * @param value - a value from the report.
 * @returns the display text.
 */
function formatValue(value: unknown): string {
  return typeof value === "number" ? value.toLocaleString("vi-VN", { maximumFractionDigits: 7, useGrouping: false }) : String(value)
}

/**
 * Errors or warnings, each with its field, the value typed, the reason and a suggestion.
 * @param props.title - list heading.
 * @param props.tone - "error" or "warning", shown with an icon as well as color.
 * @param props.problems - the problems.
 * @returns the list element.
 */
function ProblemList({ title, tone, problems }: { title: string; tone: "error" | "warning"; problems: ListingReport["errors"] }) {
  const Icon = tone === "error" ? XCircle : AlertTriangle
  return (
    <div>
      <h3 className="text-sm font-medium text-foreground">{title}</h3>
      <ul className="mt-2 space-y-2 text-sm">
        {problems.map((problem, index) => (
          <li key={index} className="flex gap-2">
            <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", tone === "error" ? "text-destructive" : "text-amber-500")} aria-hidden />
            <div>
              <p className="text-foreground"><span className="font-medium">{problem.label}</span>: {problem.message}</p>
              {problem.suggestion && <p className="text-xs text-muted-foreground">{problem.suggestion}</p>}
            </div>
          </li>
        ))}
      </ul>
    </div>
  )
}
