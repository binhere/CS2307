// Shrinks listing photos in the browser before upload: one gallery size (longest side 1280 px) and
// one card thumbnail (480 px), both JPEG. Re-encoding also drops EXIF metadata such as the GPS
// position where the photo was taken. The server checks the results again; this only saves upload
// time and storage.

/** Size and encoding of one output. */
export interface PhotoSize {
  maximumSide: number
  maximumBytes: number
  /** JPEG qualities to try in order until the file fits maximumBytes. */
  qualities: readonly number[]
}

export const GALLERY_SIZE: PhotoSize = { maximumSide: 1280, maximumBytes: 1_500_000, qualities: [0.82, 0.72, 0.6] }
export const THUMBNAIL_SIZE: PhotoSize = { maximumSide: 480, maximumBytes: 200_000, qualities: [0.78, 0.65, 0.5] }

/**
 * Dimensions that fit within a square of maximumSide, keeping the aspect ratio; never enlarges.
 * @param width - original width in pixels.
 * @param height - original height in pixels.
 * @param maximumSide - largest allowed width or height.
 * @returns whole-pixel width and height, at least 1 each.
 */
export function fitWithin(width: number, height: number, maximumSide: number): { width: number; height: number } {
  const scale = Math.min(1, maximumSide / Math.max(width, height))
  return { width: Math.max(1, Math.round(width * scale)), height: Math.max(1, Math.round(height * scale)) }
}

/**
 * Encode an image at one size, lowering quality until it fits the byte limit.
 * @param bitmap - the decoded photo (orientation already applied).
 * @param size - target size and qualities.
 * @returns a JPEG blob.
 * @throws Error when even the lowest quality is too large.
 */
async function encodeAt(bitmap: ImageBitmap, size: PhotoSize): Promise<Blob> {
  const { width, height } = fitWithin(bitmap.width, bitmap.height, size.maximumSide)
  const canvas = document.createElement("canvas")
  canvas.width = width
  canvas.height = height
  const context = canvas.getContext("2d")
  if (!context) throw new Error("Trình duyệt không hỗ trợ xử lý ảnh.")
  // JPEG has no transparency: paint transparent PNG areas white instead of black.
  context.fillStyle = "#ffffff"
  context.fillRect(0, 0, width, height)
  context.imageSmoothingQuality = "high"
  context.drawImage(bitmap, 0, 0, width, height)
  for (const quality of size.qualities) {
    const blob = await new Promise<Blob | null>((resolve) => canvas.toBlob(resolve, "image/jpeg", quality))
    if (blob && blob.size <= size.maximumBytes) return blob
  }
  throw new Error("Ảnh vẫn quá nặng sau khi nén.")
}

/** A photo ready to upload. */
export interface PreparedPhoto {
  gallery: Blob
  thumbnail: Blob
}

/**
 * Shrink one photo to the gallery and thumbnail sizes.
 * @param file - the photo the user picked (JPEG, PNG, WebP, or HEIC where the browser decodes it).
 * @returns both JPEG versions.
 * @throws Error with a Vietnamese message when the browser cannot read the photo.
 */
export async function preparePhoto(file: Blob): Promise<PreparedPhoto> {
  let bitmap: ImageBitmap
  try {
    bitmap = await createImageBitmap(file, { imageOrientation: "from-image" })
  } catch {
    throw new Error("Không đọc được ảnh này. Vui lòng dùng ảnh JPEG, PNG hoặc WebP.")
  }
  try {
    return { gallery: await encodeAt(bitmap, GALLERY_SIZE), thumbnail: await encodeAt(bitmap, THUMBNAIL_SIZE) }
  } finally {
    bitmap.close()
  }
}
