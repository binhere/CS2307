// Tests for posting listings from a zip file: finding listing folders and their photos, reading
// listing.json leniently, and the upload sequence (check everything first, post one at a time,
// report every failure with its reason, stop when signed out or asked to).
// Run with: npm test (node --test, TypeScript sources loaded with --experimental-strip-types).
import assert from "node:assert/strict"
import { test } from "node:test"
import { BlobReader, BlobWriter, TextReader, ZipWriter } from "@zip.js/zip.js"

const archive = await import("./listing-archive.ts")

/**
 * Build a zip in memory.
 * @param files - path -> text content (photos are a few placeholder bytes).
 * @returns the zip as a Blob.
 */
async function zipOf(files) {
  const writer = new ZipWriter(new BlobWriter("application/zip"))
  for (const [path, content] of Object.entries(files)) {
    await writer.add(path, typeof content === "string" ? new TextReader(content) : new BlobReader(content))
  }
  return writer.close()
}

const photo = () => new Blob([new Uint8Array([255, 216, 255])])

test("listing folders are found with their photos in file-name order", async () => {
  const opened = await archive.readListingArchive(await zipOf({
    "tin/tin-10/listing.json": '{"title": "Mười"}',
    "tin/tin-2/listing.json": '﻿{"listing_id": "rieng", "title": "Hai"}',
    "tin/tin-2/10.jpg": photo(), "tin/tin-2/2.JPG": photo(), "tin/tin-2/ghi-chu.txt": "bỏ qua",
    "tin/tin-10/1.png": photo(),
    "tin/le/anh.jpg": photo(),
    "__MACOSX/tin/tin-2/._2.JPG": photo(), "tin/tin-2/.DS_Store": "x",
  }))
  assert.deepEqual(opened.listings.map((listing) => listing.folder), ["tin/tin-2", "tin/tin-10"])
  const [second, tenth] = opened.listings
  assert.deepEqual(second.document, { listing_id: "rieng", title: "Hai" })
  assert.deepEqual(second.photos.map((item) => item.name), ["2.JPG", "10.jpg"])
  assert.equal((await second.photos[0].read()).type, "image/jpeg")
  assert.deepEqual(tenth.document, { title: "Mười", listing_id: "tin-10" }) // the folder names the listing
  assert.equal(opened.strayPhotoCount, 1)
  await opened.close()
})

test("an unreadable listing.json is reported, and a file that is not a zip is refused", async () => {
  const opened = await archive.readListingArchive(await zipOf({ "a/listing.json": "{title: 1}", "b/listing.json": "[1]" }))
  assert.match(opened.listings[0].readError, /JSON hợp lệ/)
  assert.match(opened.listings[1].readError, /JSON object/)
  await assert.rejects(archive.readListingArchive(new Blob(["not a zip"])), /zip/)
  assert.equal(archive.splitArchivePath("tin\\tin-01\\1.jpg").folder, "tin/tin-01")
})

/** A validation report as the server sends it. */
const report = (index, errors = [], metadata = {}) => ({
  index, reference: null, is_valid: errors.length === 0, listing: { metadata }, errors, warnings: [], conversions: [],
})
const problem = (label, message) => ({ field: label, label, value: null, code: "X", message })
const listing = (folder, document, photoCount = 1) => ({
  folder, document, photos: Array.from({ length: photoCount }, (_, position) => ({ name: `${position + 1}.jpg`, read: async () => photo() })),
})

/**
 * Steps that answer from fixed data and record what was posted.
 * @param invalid - listing_id -> problems the server reports for it.
 * @param postAnswers - listing_id -> the post result (defaults to posted, id from the call order).
 */
function fakeSteps(invalid = {}, postAnswers = {}) {
  const posted = []
  const validations = []
  return {
    posted, validations,
    steps: {
      validate: async (documents) => {
        validations.push(documents.length)
        return documents.map((document, index) => report(index, invalid[document.listing_id] ?? []))
      },
      prepare: async (blob) => {
        if (blob.size === 0) throw new Error("Không đọc được ảnh này.")
        return { gallery: blob, thumbnail: blob }
      },
      post: async (document, photos) => {
        posted.push({ id: document.listing_id, photoCount: photos.length })
        return postAnswers[document.listing_id] ?? { isPosted: true, id: 9000000000 + posted.length, hasCoordinates: "latitude" in (document.metadata ?? {}) }
      },
    },
  }
}

test("every listing is checked first; only acceptable ones are posted, the rest say why", async () => {
  const listings = [
    listing("tin-01", { listing_id: "tin-01", metadata: { latitude: 10.7, longitude: 106.7 } }, 2),
    listing("tin-02", { listing_id: "tin-02" }),
    { folder: "zip/tin-03", readError: "listing.json không phải JSON hợp lệ.", photos: [] },
    listing("tin-04", { listing_id: "tin-04" }, 0),
    listing("tin-05", { listing_id: "tin-05" }),
    listing("tin-06", { listing_id: "tin-06" }),
  ]
  const { steps, posted } = fakeSteps(
    { "tin-02": [problem("Tiêu đề", "Cần ít nhất 10 ký tự, hiện có 3."), problem("Mô tả", "Thiếu trường bắt buộc «Mô tả» (description).")] },
    { "tin-06": { isPosted: true, id: 9000000042, hasCoordinates: true, wasPostedBefore: true } },
  )
  const progress = []
  const result = await archive.uploadArchivedListings(listings, steps, (item) => progress.push(item))
  assert.deepEqual(posted, [{ id: "tin-01", photoCount: 2 }, { id: "tin-05", photoCount: 1 }, { id: "tin-06", photoCount: 1 }])
  assert.deepEqual(result.outcomes.map((outcome) => outcome.status), ["posted", "failed", "failed", "failed", "posted", "posted"])
  assert.equal(result.outcomes[0].hasCoordinates, true)
  assert.equal(result.outcomes[4].hasCoordinates, false)
  // Uploading a fixed zip again: listings that went through before point at their first copy.
  assert.deepEqual([result.outcomes[5].id, result.outcomes[5].wasPostedBefore, result.outcomes[4].wasPostedBefore], [9000000042, true, false])
  assert.deepEqual(result.outcomes[1].reasons, ["Tiêu đề: Cần ít nhất 10 ký tự, hiện có 3.", "Thiếu trường bắt buộc «Mô tả» (description)."])
  assert.deepEqual(result.outcomes[2].reasons, ["listing.json không phải JSON hợp lệ.", "Cần ít nhất 1 ảnh."]) // all problems at once
  assert.equal(result.outcomes[2].reference, "tin-03")
  assert.deepEqual(result.outcomes[3].reasons, ["Cần ít nhất 1 ảnh."])
  assert.deepEqual(progress.at(-1), { finished: 6, total: 6 })
  assert.equal(result.stopReason, undefined)
})

test("a reason names its field once", () => {
  assert.deepEqual(archive.problemLines([
    problem("Giá", "Giá phải là con số cụ thể."), problem("Diện tích", "«rộng» không phải là một con số."),
  ]), ["Giá phải là con số cụ thể.", "Diện tích: «rộng» không phải là một con số."])
})

test("a listing with more than 20 photos is reported at the end and does not stop the others", async () => {
  const listings = [listing("nhieu-anh", { listing_id: "nhieu-anh" }, 25), listing("tin-02", { listing_id: "tin-02" }, 3)]
  const { steps, posted } = fakeSteps()
  const result = await archive.uploadArchivedListings(listings, steps, () => undefined)
  assert.deepEqual(posted, [{ id: "tin-02", photoCount: 3 }])
  assert.deepEqual(result.outcomes[0], { folder: "nhieu-anh", reference: "nhieu-anh", status: "failed", reasons: ["Tối đa 20 ảnh, hiện có 25."] })
  assert.equal(result.outcomes[1].status, "posted")
})

test("checks go in batches of 100 and a photo that cannot be read fails only its listing", async () => {
  const listings = Array.from({ length: 205 }, (_, index) => listing(`tin-${index}`, { listing_id: `tin-${index}` }))
  listings[3].photos[0].read = async () => new Blob([])
  const { steps, validations, posted } = fakeSteps()
  const result = await archive.uploadArchivedListings(listings, steps, () => undefined)
  assert.deepEqual(validations, [100, 100, 5])
  assert.equal(posted.length, 204)
  assert.deepEqual(result.outcomes[3].reasons, ["1.jpg: Không đọc được ảnh này."])
})

test("signing out stops the upload, and so does the stop button; the rest stay unposted", async () => {
  const listings = ["a", "b", "c"].map((id) => listing(id, { listing_id: id }))
  const signedOut = fakeSteps({}, { b: { isPosted: false, reasons: ["Phiên đăng nhập đã hết hạn."], shouldStop: true } })
  const stopped = await archive.uploadArchivedListings(listings, signedOut.steps, () => undefined)
  assert.deepEqual(stopped.outcomes.map((outcome) => outcome.status), ["posted", "not_posted", "not_posted"])
  assert.equal(stopped.stopReason, "Phiên đăng nhập đã hết hạn.")

  const controller = new AbortController()
  const asked = fakeSteps()
  const result = await archive.uploadArchivedListings(listings, asked.steps, (progress) => {
    if (progress.finished === 1) controller.abort()
  }, controller.signal)
  assert.deepEqual(result.outcomes.map((outcome) => outcome.status), ["posted", "not_posted", "not_posted"])
  assert.match(result.stopReason, /dừng/)
})

test("the sample archive opens as two valid-looking listings with photos", async () => {
  const sample = await archive.buildSampleArchive(async () => photo())
  const opened = await archive.readListingArchive(sample)
  assert.deepEqual(opened.listings.map((item) => [item.folder, item.photos.length, item.document.listing_id]),
    [["tin-01", 2, "tin-01"], ["tin-02", 1, "nha-pho-ben-thanh"]])
  await opened.close()
})
