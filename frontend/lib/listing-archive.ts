// Posting many listings from one zip file, entirely in the browser. The zip is read in place (zip.js
// reads only the parts it needs, so an archive of original photos larger than memory still opens);
// each listing's photos are shrunk on the user's machine like the form's, and the listings are
// posted one request at a time, because Cloudflare refuses request bodies over 100 MB and a whole
// archive easily exceeds that. Listings without coordinates are saved as they are; a scheduled job
// on the server looks their street up afterwards.
//
// Layout: one folder per listing, holding listing.json (the same fields as the form) and the
// listing's photos, shown in file-name order. A listing without listing_id takes its folder's name.

import { BlobReader, BlobWriter, TextReader, TextWriter, ZipReader, ZipWriter, configure } from "@zip.js/zip.js"
import type { Entry, FileEntry } from "@zip.js/zip.js"
import type { PreparedPhoto } from "./image-resize"
import type { ListingProblem, ListingReport } from "./listing-submission"

// Web workers would load zip.js code from a blob URL; the photos are already compressed, so
// unpacking them on the main thread costs little.
configure({ useWebWorkers: false })

export const LISTING_FILE_NAME = "listing.json"
/** The server's limits on one listing's photos (storage.py); the server checks them again. */
export const MAXIMUM_PHOTOS = 20
/** Listings checked per validation request (the endpoint's maximum). */
export const VALIDATION_BATCH_SIZE = 100
const PHOTO_TYPES: Record<string, string> = {
  jpg: "image/jpeg", jpeg: "image/jpeg", png: "image/png", webp: "image/webp", avif: "image/avif",
  heic: "image/heic", heif: "image/heif",
}

/** A photo inside the archive, unpacked only when it is about to be shrunk. */
export interface ArchivedPhoto {
  name: string
  read: () => Promise<Blob>
}

/** One listing folder of the archive. */
export interface ArchivedListing {
  /** Folder path inside the archive ("" for the root), shown in the results. */
  folder: string
  /** listing.json as an object, with listing_id defaulting to the folder name; absent when unreadable. */
  document?: Record<string, unknown>
  /** Why listing.json cannot be used. */
  readError?: string
  photos: ArchivedPhoto[]
}

/** What an archive holds. */
export interface ListingArchive {
  listings: ArchivedListing[]
  /** Photos outside every listing folder; they are ignored. */
  strayPhotoCount: number
  /** Releases the archive reader. */
  close: () => Promise<void>
}

/** Numeric-aware order, so "2.jpg" comes before "10.jpg" and "tin-2" before "tin-10". */
const compareNames = (first: string, second: string) => first.localeCompare(second, "vi", { numeric: true })

/**
 * Split an archive path into its folder and file name, ignoring macOS and hidden entries.
 * @param path - entry path as stored in the zip.
 * @returns the folder ("" at the root) and file name, or null for an entry to skip.
 */
export function splitArchivePath(path: string): { folder: string; name: string } | null {
  const parts = path.replace(/\\/g, "/").split("/").filter(Boolean)
  if (parts.length === 0 || parts.some((part) => part === "__MACOSX" || part.startsWith("."))) return null
  return { folder: parts.slice(0, -1).join("/"), name: parts[parts.length - 1] }
}

/**
 * Turn the text of a listing.json into the listing document.
 * @param text - the file's content.
 * @param folder - its folder, whose last part becomes listing_id when the file has none.
 * @returns the document, or a Vietnamese reason it cannot be used.
 */
export function parseListingFile(text: string, folder: string): { document: Record<string, unknown> } | { readError: string } {
  let value: unknown
  try {
    value = JSON.parse(text.replace(/^﻿/, ""))
  } catch {
    return { readError: `${LISTING_FILE_NAME} không phải JSON hợp lệ.` }
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return { readError: `${LISTING_FILE_NAME} phải là một JSON object {…}.` }
  }
  const document = { ...(value as Record<string, unknown>) }
  const folderName = folder.split("/").pop()
  if ((document.listing_id === undefined || document.listing_id === null || document.listing_id === "") && folderName) {
    document.listing_id = folderName
  }
  return { document }
}

/**
 * Open a zip file and find its listing folders.
 * @param file - the zip the user picked.
 * @returns the listings in folder-name order, the number of ignored photos, and a close function.
 * @throws Error with a Vietnamese message when the file is not a readable zip.
 */
export async function readListingArchive(file: Blob): Promise<ListingArchive> {
  const reader = new ZipReader(new BlobReader(file))
  let entries: Entry[]
  try {
    entries = await reader.getEntries()
  } catch {
    await reader.close().catch(() => undefined)
    throw new Error("Không đọc được file zip này.")
  }
  const listingFiles = new Map<string, FileEntry>()
  const photosByFolder = new Map<string, ArchivedPhoto[]>()
  for (const entry of entries) {
    if (entry.directory) continue
    const location = splitArchivePath(entry.filename)
    if (!location) continue
    const extension = location.name.split(".").pop()?.toLowerCase() ?? ""
    if (location.name.toLowerCase() === LISTING_FILE_NAME) {
      listingFiles.set(location.folder, entry)
    } else if (extension in PHOTO_TYPES) {
      const photos = photosByFolder.get(location.folder) ?? []
      photos.push({ name: location.name, read: () => entry.getData(new BlobWriter(PHOTO_TYPES[extension])) })
      photosByFolder.set(location.folder, photos)
    }
  }
  const listings: ArchivedListing[] = []
  for (const folder of [...listingFiles.keys()].sort(compareNames)) {
    const entry = listingFiles.get(folder)!
    let parsed: { document: Record<string, unknown> } | { readError: string }
    try {
      parsed = parseListingFile(await entry.getData(new TextWriter()), folder)
    } catch {
      parsed = { readError: `Không đọc được ${LISTING_FILE_NAME}.` }
    }
    const photos = (photosByFolder.get(folder) ?? []).sort((first, second) => compareNames(first.name, second.name))
    listings.push({ folder, ...parsed, photos })
  }
  let strayPhotoCount = 0
  for (const [folder, photos] of photosByFolder) if (!listingFiles.has(folder)) strayPhotoCount += photos.length
  return { listings, strayPhotoCount, close: () => reader.close().then(() => undefined) }
}

/** What happened to one listing of the archive. */
export type ListingOutcome =
  /** wasPostedBefore: the user had already posted the same listing; id is that earlier listing. */
  | { folder: string; reference: string; status: "posted"; id: number; hasCoordinates: boolean; wasPostedBefore: boolean }
  | { folder: string; reference: string; status: "failed"; reasons: string[] }
  | { folder: string; reference: string; status: "not_posted" }

/** The answer to posting one listing. */
export type PostResult =
  | { isPosted: true; id: number; hasCoordinates: boolean; wasPostedBefore?: boolean }
  /** shouldStop: the upload cannot go on (signed out, offline, server down); the listing stays unposted. */
  | { isPosted: false; reasons: string[]; shouldStop?: boolean }

/** The network and image work of an upload, passed in so the sequence can be tested without them. */
export interface UploadSteps {
  /** Check up to VALIDATION_BATCH_SIZE documents; one report per document, in order. Throws when the check fails. */
  validate: (documents: Record<string, unknown>[]) => Promise<ListingReport[]>
  /** Shrink one photo to its two sizes. Throws with a Vietnamese message when it cannot. */
  prepare: (photo: Blob) => Promise<PreparedPhoto>
  /** Post one listing with its photos. */
  post: (document: Record<string, unknown>, photos: PreparedPhoto[]) => Promise<PostResult>
}

/** Progress of an upload, for the progress bar. */
export interface UploadProgress {
  finished: number
  total: number
  /** The listing being posted now. */
  current?: string
}

/** The result of an upload. */
export interface UploadResult {
  outcomes: ListingOutcome[]
  /** Why the upload stopped before the end, when it did. */
  stopReason?: string
}

/**
 * One line per problem the server reported, prefixed with the field's label unless the message
 * already starts with it or quotes it (no "Giá: Giá phải là…").
 * @param problems - errors from a listing report.
 * @returns Vietnamese lines for the result list.
 */
export function problemLines(problems: ListingProblem[]): string[] {
  return problems.map((problem) => {
    const isNamed = problem.message.startsWith(problem.label) || problem.message.includes(`«${problem.label}»`)
    return problem.label && !isNamed ? `${problem.label}: ${problem.message}` : problem.message
  })
}

/**
 * Problems with a listing's number of photos, checked before any photo is shrunk.
 * @param count - photos in the listing's folder.
 * @returns Vietnamese lines, empty when the count is fine (same wording as the server).
 */
export function photoCountProblems(count: number): string[] {
  if (count === 0) return ["Cần ít nhất 1 ảnh."]
  if (count > MAXIMUM_PHOTOS) return [`Tối đa ${MAXIMUM_PHOTOS} ảnh, hiện có ${count}.`]
  return []
}

/**
 * Check every listing, then post the acceptable ones one at a time with their shrunk photos.
 * Listings with data or photo-count problems are reported without being posted; a listing whose
 * photo cannot be read or which the server refuses is reported and the upload goes on. When the
 * upload cannot go on (signed out, offline, server down) it stops, and that listing and the rest
 * stay "not_posted", so running the upload again on those posts nothing twice.
 * @param listings - the archive's listings.
 * @param steps - validation, photo shrinking and posting.
 * @param onProgress - called after each finished listing and when a posting starts.
 * @param signal - stops the upload after the listing being posted.
 * @returns one outcome per listing, in archive order, and why the upload stopped early.
 * @throws Error when the validation step throws (nothing has been posted then).
 */
export async function uploadArchivedListings(
  listings: ArchivedListing[], steps: UploadSteps, onProgress: (progress: UploadProgress) => void, signal?: AbortSignal,
): Promise<UploadResult> {
  const total = listings.length
  const referenceOf = (listing: ArchivedListing) =>
    String(listing.document?.listing_id ?? (listing.folder.split("/").pop() || LISTING_FILE_NAME))
  const outcomes: (ListingOutcome | undefined)[] = new Array(total)
  const readable = listings.flatMap((listing, index) => (listing.document ? [index] : []))
  const reports = new Map<number, ListingReport>()
  for (let start = 0; start < readable.length; start += VALIDATION_BATCH_SIZE) {
    const batch = readable.slice(start, start + VALIDATION_BATCH_SIZE)
    const batchReports = await steps.validate(batch.map((index) => listings[index].document!))
    batchReports.forEach((report, position) => reports.set(batch[report.index ?? position], report))
  }
  const postable: number[] = []
  listings.forEach((listing, index) => {
    const report = reports.get(index)
    const reasons = listing.readError ? [listing.readError] : problemLines(report && !report.is_valid ? report.errors : [])
    reasons.push(...photoCountProblems(listing.photos.length))
    if (reasons.length > 0) outcomes[index] = { folder: listing.folder, reference: referenceOf(listing), status: "failed", reasons }
    else postable.push(index)
  })
  let finished = total - postable.length
  onProgress({ finished, total })
  let stopReason: string | undefined
  for (const index of postable) {
    if (signal?.aborted) {
      stopReason = "Đã dừng theo yêu cầu."
      break
    }
    const listing = listings[index]
    const reference = referenceOf(listing)
    onProgress({ finished, total, current: reference })
    const prepared: PreparedPhoto[] = []
    let photoProblem: string | undefined
    for (const photo of listing.photos) {
      try {
        prepared.push(await steps.prepare(await photo.read()))
      } catch (error) {
        photoProblem = `${photo.name}: ${error instanceof Error ? error.message : "Không đọc được ảnh này."}`
        break
      }
    }
    if (photoProblem) {
      outcomes[index] = { folder: listing.folder, reference, status: "failed", reasons: [photoProblem] }
    } else {
      const result = await steps.post(listing.document!, prepared)
      if (result.isPosted) {
        outcomes[index] = {
          folder: listing.folder, reference, status: "posted", id: result.id,
          hasCoordinates: result.hasCoordinates, wasPostedBefore: result.wasPostedBefore ?? false,
        }
      } else if (result.shouldStop) {
        // Not refused for its content (signed out, offline, server down): left unposted, to resume later.
        stopReason = result.reasons[0]
        break
      } else {
        outcomes[index] = { folder: listing.folder, reference, status: "failed", reasons: result.reasons }
      }
    }
    finished += 1
    onProgress({ finished, total })
    if (stopReason) break
  }
  return {
    outcomes: listings.map((listing, index) =>
      outcomes[index] ?? { folder: listing.folder, reference: referenceOf(listing), status: "not_posted" }),
    stopReason,
  }
}

/** The two listings of the sample archive: one in the form's exact codes, one written loosely. */
export const SAMPLE_LISTINGS: { folder: string; document: Record<string, unknown>; photoCount: number }[] = [
  {
    folder: "tin-01",
    photoCount: 2,
    document: {
      title: "Bán căn hộ 2 phòng ngủ view sông, sổ hồng riêng",
      description: "Căn hộ 72 m², 2 phòng ngủ, 2 phòng tắm, ban công hướng đông, nội thất cơ bản. Gần trường học và chợ.",
      property_type: "can_ho_chung_cu",
      price_billion_vnd: 4.5,
      area_m2: 72,
      ward: "Phường Cát Lái",
      former_district: "Thành phố Thủ Đức",
      posted_date: "2026-11-20",
      metadata: { bedroom_count: 2, bathroom_count: 2, legal_status: "so_hong_rieng", furnishing: "co_ban", street: "Đường Nguyễn Thị Định" },
    },
  },
  {
    folder: "tin-02",
    photoCount: 1,
    document: {
      listing_id: "nha-pho-ben-thanh",
      title: "Nhà mặt phố 1 trệt 2 lầu, tiện kinh doanh",
      description: "Nhà mặt tiền 4 x 15 m, 3 phòng ngủ, đang cho thuê. Sổ hồng riêng, xem nhà bất cứ lúc nào.",
      property_type: "Nhà mặt phố",
      price_billion_vnd: "28 tỷ 500",
      area_m2: "60m2",
      ward: "Phường Bến Thành",
      former_district: "Quận 1",
      posted_date: "20/11/2026",
      metadata: { bedroom_count: "3 PN", legal_status: "sổ hồng", latitude: 10.7725, longitude: 106.698 },
    },
  },
]

/**
 * Build the sample archive users can download and copy.
 * @param makePhoto - draws a placeholder photo with a label such as "tin-01 · 1".
 * @returns the zip file.
 */
export async function buildSampleArchive(makePhoto: (label: string) => Promise<Blob>): Promise<Blob> {
  const writer = new ZipWriter(new BlobWriter("application/zip"))
  for (const sample of SAMPLE_LISTINGS) {
    await writer.add(`${sample.folder}/${LISTING_FILE_NAME}`, new TextReader(`${JSON.stringify(sample.document, null, 2)}\n`))
    for (let position = 1; position <= sample.photoCount; position += 1) {
      // JPEG is already compressed; storing it as is keeps the sample quick to build.
      await writer.add(`${sample.folder}/${position}.jpg`, new BlobReader(await makePhoto(`${sample.folder} · ${position}`)), { level: 0 })
    }
  }
  return writer.close()
}
