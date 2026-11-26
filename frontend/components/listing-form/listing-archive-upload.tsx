"use client"

// Posting many listings at once from a zip file: one folder per listing with its listing.json and
// photos; the downloadable sample shows the layout, so the panel has no instructions. The browser
// opens the zip, the server checks every listing first, then the browser posts the acceptable ones
// one by one with photos shrunk like the form's. A listing that breaks a rule (such as more than 20
// photos) never stops the upload: it is listed with its reasons once the others are posted. An
// upload that had to stop (signed out, offline, server down, or the user pressed stop) can be
// resumed for the listings not posted yet.

import { useEffect, useRef, useState } from "react"
import Link from "next/link"
import { AlertTriangle, CheckCircle2, Download, FileArchive, Loader2, XCircle } from "lucide-react"
import { Button } from "@/components/ui/button"
import { authHeaders, getStoredToken } from "@/lib/auth-context"
import { HttpError, extractError, fetchJson, type ApiEnvelope } from "@/lib/http"
import { preparePhoto } from "@/lib/image-resize"
import {
  LISTING_FILE_NAME,
  buildSampleArchive,
  problemLines,
  readListingArchive,
  uploadArchivedListings,
  type ArchivedListing,
  type ListingArchive,
  type ListingOutcome,
  type PostResult,
  type UploadProgress,
  type UploadSteps,
} from "@/lib/listing-archive"
import type { ListingReport } from "@/lib/listing-submission"

// Signing in on this page would lose the opened zip; the token is shared with other tabs.
const SIGNED_OUT = "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại trong một tab mới, rồi quay lại bấm Đăng tiếp."
const SAMPLE_FILE_NAME = "tin-mau.zip"

/**
 * Check listings on the server, as one validation request.
 * @param documents - up to 100 listing documents.
 * @returns one report per document, in order.
 * @throws HttpError or Error when the check cannot be made.
 */
async function validateListings(documents: Record<string, unknown>[]): Promise<ListingReport[]> {
  const envelope = await fetchJson<ApiEnvelope<{ results: ListingReport[] }>>("/api/listings/validate", {
    method: "POST", headers: authHeaders(), body: JSON.stringify({ listings: documents }),
  }, "Chưa kiểm tra được các tin.")
  return envelope.data?.results ?? []
}

/**
 * Post one listing with its prepared photos, turning every failure into reasons for the result list.
 * @param document - the listing.json document.
 * @param photos - its photos at both sizes, in order.
 * @returns the new listing, or why it was not posted and whether the upload must stop.
 */
async function postListing(document: Record<string, unknown>, photos: { gallery: Blob; thumbnail: Blob }[]): Promise<PostResult> {
  const body = new FormData()
  body.append("listing", JSON.stringify(document))
  photos.forEach((photo, index) => {
    body.append("photos", photo.gallery, `photo-${index + 1}.jpg`)
    body.append("thumbnails", photo.thumbnail, `photo-${index + 1}.jpg`)
  })
  const token = getStoredToken()
  try {
    const envelope = await fetchJson<ApiEnvelope<{ id: number; report: ListingReport }>>("/api/listings", {
      method: "POST", headers: token ? { Authorization: `Bearer ${token}` } : {}, body,
    }, "Chưa đăng được tin.")
    if (!envelope.data) return { isPosted: false, reasons: ["Chưa đăng được tin."], shouldStop: true }
    return { isPosted: true, id: envelope.data.id, hasCoordinates: hasCoordinates(envelope.data.report) }
  } catch (error) {
    if (!(error instanceof HttpError)) return { isPosted: false, reasons: ["Mất kết nối. Vui lòng kiểm tra mạng rồi bấm Đăng tiếp."], shouldStop: true }
    if (error.status === 401) return { isPosted: false, reasons: [SIGNED_OUT], shouldStop: true }
    const message = extractError(error.payload, error.message)
    if (error.status >= 500) return { isPosted: false, reasons: [message], shouldStop: true }
    const data = (error.payload as ApiEnvelope<{ id?: number; report?: ListingReport }> | null)?.data
    if (error.status === 409 && typeof data?.id === "number") {
      return { isPosted: true, id: data.id, hasCoordinates: data.report ? hasCoordinates(data.report) : true, wasPostedBefore: true }
    }
    const refused = data?.report
    return { isPosted: false, reasons: refused && refused.errors.length > 0 ? problemLines(refused.errors) : [message] }
  }
}

/**
 * Whether a posted listing came with coordinates.
 * @param report - the server's report of the listing.
 * @returns true when its metadata holds a latitude.
 */
function hasCoordinates(report: ListingReport): boolean {
  const metadata = (report.listing.metadata ?? {}) as Record<string, unknown>
  return "latitude" in metadata
}

const UPLOAD_STEPS: UploadSteps = { validate: validateListings, prepare: preparePhoto, post: postListing }

/**
 * Draw a placeholder photo for the sample archive.
 * @param label - text drawn on it, e.g. "tin-01 · 1".
 * @returns a JPEG blob.
 */
async function drawSamplePhoto(label: string): Promise<Blob> {
  const canvas = document.createElement("canvas")
  canvas.width = 960
  canvas.height = 640
  const context = canvas.getContext("2d")
  if (!context) throw new Error("Trình duyệt không hỗ trợ xử lý ảnh.")
  context.fillStyle = label.startsWith("tin-01") ? "#5b7f95" : "#8a6f4e"
  context.fillRect(0, 0, canvas.width, canvas.height)
  context.fillStyle = "#ffffff"
  context.font = "600 56px system-ui, sans-serif"
  context.textAlign = "center"
  context.fillText(`Ảnh mẫu ${label}`, canvas.width / 2, canvas.height / 2)
  const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", 0.8))
  if (!blob) throw new Error("Chưa tạo được ảnh mẫu.")
  return blob
}

/**
 * The zip upload panel of the posting page.
 * @returns the panel element.
 */
export function ListingArchiveUpload() {
  const fileInput = useRef<HTMLInputElement>(null)
  const [fileName, setFileName] = useState<string | null>(null)
  const [archive, setArchive] = useState<ListingArchive | null>(null)
  const [isReading, setIsReading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [progress, setProgress] = useState<UploadProgress | null>(null)
  const [outcomes, setOutcomes] = useState<ListingOutcome[] | null>(null)
  const [stopReason, setStopReason] = useState<string | undefined>(undefined)
  const [isSampleBusy, setIsSampleBusy] = useState(false)
  const controller = useRef<AbortController | null>(null)
  const archiveRef = useRef(archive)
  archiveRef.current = archive
  const isUploading = progress !== null

  useEffect(() => () => {
    controller.current?.abort()
    void archiveRef.current?.close()
  }, [])

  /**
   * Open the picked zip and list its listings.
   * @param file - the zip, or undefined when the picker was cancelled.
   */
  const openArchive = async (file: File | undefined) => {
    if (!file) return
    await archive?.close()
    setArchive(null)
    setOutcomes(null)
    setStopReason(undefined)
    setError(null)
    setFileName(file.name)
    setIsReading(true)
    try {
      const opened = await readListingArchive(file)
      setArchive(opened)
      if (opened.listings.length === 0) setError(`Không tìm thấy file ${LISTING_FILE_NAME} nào trong file zip.`)
    } catch (readError) {
      setError(readError instanceof Error ? readError.message : "Không đọc được file zip này.")
    } finally {
      setIsReading(false)
    }
  }

  /**
   * Upload some listings of the archive and merge their outcomes into the results.
   * @param listings - all listings on a first run, the unposted ones when resuming.
   */
  const upload = async (listings: ArchivedListing[]) => {
    const current = new AbortController()
    controller.current = current
    setError(null)
    setStopReason(undefined)
    setProgress({ finished: 0, total: listings.length })
    try {
      const result = await uploadArchivedListings(listings, UPLOAD_STEPS, setProgress, current.signal)
      const byFolder = new Map(result.outcomes.map((outcome) => [outcome.folder, outcome]))
      setOutcomes((previous) => previous ? previous.map((outcome) => byFolder.get(outcome.folder) ?? outcome) : result.outcomes)
      setStopReason(result.stopReason)
    } catch (uploadError) {
      setError(uploadError instanceof HttpError && uploadError.status === 401
        ? "Phiên đăng nhập đã hết hạn. Vui lòng đăng nhập lại."
        : uploadError instanceof Error ? uploadError.message : "Chưa kiểm tra được các tin.")
    } finally {
      setProgress(null)
    }
  }

  /** Download the sample archive. */
  const downloadSample = async () => {
    setIsSampleBusy(true)
    try {
      const url = URL.createObjectURL(await buildSampleArchive(drawSamplePhoto))
      const link = document.createElement("a")
      link.href = url
      link.download = SAMPLE_FILE_NAME
      link.click()
      setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (sampleError) {
      setError(sampleError instanceof Error ? sampleError.message : "Chưa tạo được file mẫu.")
    } finally {
      setIsSampleBusy(false)
    }
  }

  const unposted = outcomes && archive
    ? archive.listings.filter((listing) => outcomes.find((outcome) => outcome.folder === listing.folder)?.status === "not_posted")
    : []
  const posted = outcomes?.filter((outcome): outcome is Extract<ListingOutcome, { status: "posted" }> => outcome.status === "posted") ?? []
  const failed = outcomes?.filter((outcome): outcome is Extract<ListingOutcome, { status: "failed" }> => outcome.status === "failed") ?? []
  const newlyPosted = posted.filter((outcome) => !outcome.wasPostedBefore)
  const postedBefore = posted.length - newlyPosted.length
  const withoutCoordinates = newlyPosted.filter((outcome) => !outcome.hasCoordinates).length
  const percent = progress && progress.total > 0 ? Math.round((progress.finished / progress.total) * 100) : 0

  return (
    <div className="space-y-6">
      <section className="space-y-4 rounded-xl border border-border bg-card p-4 sm:p-6" aria-labelledby="archive-upload">
        <h2 id="archive-upload" className="text-base font-semibold text-foreground">Tải lên file zip</h2>
        <div className="flex flex-wrap gap-3">
          <input ref={fileInput} type="file" accept=".zip,application/zip" className="hidden" tabIndex={-1} aria-hidden
            onChange={(event) => { void openArchive(event.target.files?.[0]); event.target.value = "" }} />
          <Button type="button" variant={archive ? "outline" : "default"} disabled={isUploading || isReading}
            onClick={() => fileInput.current?.click()}>
            <FileArchive className="h-4 w-4" aria-hidden />{archive ? "Chọn file khác" : "Chọn file zip"}
          </Button>
          <Button type="button" variant="outline" onClick={() => void downloadSample()} disabled={isSampleBusy}>
            {isSampleBusy ? <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden /> : <Download className="h-4 w-4" aria-hidden />}
            Tải file mẫu
          </Button>
        </div>
      </section>

      {isReading && (
        <p role="status" className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin motion-reduce:animate-none" aria-hidden />Đang đọc {fileName}
        </p>
      )}
      {error && <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">{error}</p>}

      {archive && archive.listings.length > 0 && !outcomes && !isUploading && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card p-4 text-sm">
          <div>
            <p className="font-medium text-foreground">{fileName}: {archive.listings.length} tin</p>
            {archive.strayPhotoCount > 0 && (
              <p className="text-muted-foreground">{archive.strayPhotoCount} ảnh không nằm trong thư mục tin nào sẽ được bỏ qua.</p>
            )}
          </div>
          <Button type="button" onClick={() => void upload(archive.listings)}>Đăng {archive.listings.length} tin</Button>
        </div>
      )}

      {progress && (
        <div className="space-y-2 rounded-xl border border-border bg-card p-4 text-sm">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p role="status" className="text-foreground">
              {progress.current ? <>Đang đăng <span className="font-medium">{progress.current}</span></> : "Đang kiểm tra các tin"}
              <span className="text-muted-foreground"> · {progress.finished}/{progress.total}</span>
            </p>
            <Button type="button" variant="outline" size="sm" onClick={() => controller.current?.abort()}>Dừng</Button>
          </div>
          <div role="progressbar" aria-label="Tiến độ đăng tin" aria-valuemin={0} aria-valuemax={progress.total} aria-valuenow={progress.finished}
            className="h-2 overflow-hidden rounded-full bg-muted">
            <div className="h-full rounded-full bg-primary transition-[width] duration-300 motion-reduce:transition-none" style={{ width: `${percent}%` }} />
          </div>
        </div>
      )}

      {outcomes && !isUploading && (
        <div className="space-y-4">
          <div role="status" className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card p-4 text-sm">
            <p className="text-foreground">
              {[`Đã đăng ${newlyPosted.length} tin`, postedBefore > 0 && `${postedBefore} tin đã đăng trước đó`,
                failed.length > 0 && `${failed.length} tin có lỗi`, unposted.length > 0 && `${unposted.length} tin chưa đăng`]
                .filter(Boolean).join(" · ")}
            </p>
            {unposted.length > 0 && <Button type="button" onClick={() => void upload(unposted)}>Đăng tiếp {unposted.length} tin</Button>}
          </div>
          {stopReason && <p role="alert" className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive">{stopReason}</p>}
          {withoutCoordinates > 0 && (
            <p className="text-sm text-muted-foreground">{withoutCoordinates} tin chưa có tọa độ. Hệ thống sẽ tự tìm tọa độ từ địa chỉ trong vài phút.</p>
          )}

          {failed.length > 0 && (
            <section aria-labelledby="archive-failed" className="rounded-xl border border-border bg-card p-4 sm:p-6">
              <h3 id="archive-failed" className="text-sm font-semibold text-foreground">Tin có lỗi</h3>
              <ul className="mt-3 divide-y divide-border">
                {failed.map((outcome) => (
                  <li key={outcome.folder} className="py-3 text-sm first:pt-0 last:pb-0">
                    <p className="flex flex-wrap items-baseline gap-x-2">
                      <XCircle className="h-4 w-4 shrink-0 self-center text-destructive" aria-hidden />
                      <span className="font-medium text-foreground">{outcome.reference}</span>
                      {outcome.folder && outcome.folder !== outcome.reference && <span className="font-mono text-xs text-muted-foreground">{outcome.folder}</span>}
                    </p>
                    <ul className="mt-1 list-disc space-y-0.5 pl-10 text-muted-foreground">
                      {outcome.reasons.map((reason) => <li key={reason}>{reason}</li>)}
                    </ul>
                  </li>
                ))}
              </ul>
            </section>
          )}

          {posted.length > 0 && (
            <details className="rounded-xl border border-border bg-card p-4 text-sm sm:px-6">
              <summary className="cursor-pointer font-semibold text-foreground">Tin đã đăng ({posted.length})</summary>
              <ul className="mt-3 space-y-1.5">
                {posted.map((outcome) => (
                  <li key={outcome.folder} className="flex flex-wrap items-center gap-2">
                    <CheckCircle2 className="h-4 w-4 shrink-0 text-primary" aria-hidden />
                    <span className="text-foreground">{outcome.reference}</span>
                    {outcome.wasPostedBefore && <span className="text-xs text-muted-foreground">đã đăng trước đó</span>}
                    {!outcome.wasPostedBefore && !outcome.hasCoordinates && (
                      <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                        <AlertTriangle className="h-3.5 w-3.5" aria-hidden />chưa có tọa độ
                      </span>
                    )}
                    <Link href={`/chi-tiet/${outcome.id}`} className="font-medium text-primary hover:underline">Xem tin</Link>
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}
    </div>
  )
}
