'use client'

import { useState } from 'react'
import Image from 'next/image'
import { ChevronLeft, ChevronRight, Images } from 'lucide-react'
import { getPropertyImageSrc, getPropertyThumbnailSrc, PROPERTY_IMAGE_FALLBACK } from '@/lib/property-images'
import { cn } from '@/lib/utils'

interface ImageGalleryProps {
  images: string[]
  title: string
}

export function ImageGallery({ images, title }: ImageGalleryProps) {
  const storedImages = (images || []).filter((image) => Boolean(image?.trim()))
  const propertyImages = storedImages.map(getPropertyImageSrc)
  const imageList = propertyImages.length > 0 ? propertyImages : [PROPERTY_IMAGE_FALLBACK]
  // The strip under the main photo shows small previews: posted listings have 480 px thumbnails.
  const previewList = storedImages.length > 0 ? storedImages.map(getPropertyThumbnailSrc) : [PROPERTY_IMAGE_FALLBACK]
  const [mainImageIndex, setMainImageIndex] = useState(0)
  // Photos that failed to load (e.g. not uploaded to MinIO yet) render the illustration instead;
  // kept in state so a re-render cannot swap the broken URL back in.
  const [failedImageIndexes, setFailedImageIndexes] = useState<ReadonlySet<number>>(new Set())

  /**
   * Resolve what a gallery slot displays.
   * @param index - position in imageList.
   * @returns the photo URL, or the fallback illustration when the photo is missing or failed.
   */
  const imageSourceAt = (index: number) =>
    failedImageIndexes.has(index) ? PROPERTY_IMAGE_FALLBACK : imageList[index]
  const isMainImageIllustration = imageSourceAt(mainImageIndex) === PROPERTY_IMAGE_FALLBACK

  const goToPrevious = () => {
    setMainImageIndex((prev) => (prev === 0 ? imageList.length - 1 : prev - 1))
  }

  const goToNext = () => {
    setMainImageIndex((prev) => (prev === imageList.length - 1 ? 0 : prev + 1))
  }

  /**
   * Mark a photo as failed so its slot falls back to the illustration.
   * @param index - position in imageList of the image that failed to load.
   */
  const markImageFailed = (index: number) => {
    setFailedImageIndexes((previous) => (previous.has(index) ? previous : new Set(previous).add(index)))
  }

  return (
    <div className="space-y-2">
      <div
        role="region"
        className="group relative aspect-[16/10] w-full overflow-hidden rounded-xl bg-muted"
        onKeyDown={(e) => {
          if (e.key === 'ArrowLeft') goToPrevious()
          if (e.key === 'ArrowRight') goToNext()
        }}
        tabIndex={imageList.length > 1 ? 0 : undefined}
        aria-label="Thư viện ảnh, dùng phím mũi tên để chuyển ảnh"
      >
        <Image
          src={imageSourceAt(mainImageIndex)}
          alt={title}
          fill
          className="object-cover"
          priority
          onError={() => markImageFailed(mainImageIndex)}
        />

        {isMainImageIllustration && (
          <span className="absolute bottom-3 left-3 rounded bg-black/60 px-2 py-0.5 text-xs text-white">Ảnh minh họa</span>
        )}

        {imageList.length > 1 && (
          <>
            <button
              type="button"
              aria-label="Ảnh trước"
              className="absolute left-3 top-1/2 -translate-y-1/2 rounded-full bg-white/90 p-2 text-slate-800 shadow-md transition-opacity hover:bg-white focus-visible:opacity-100 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100"
              onClick={goToPrevious}
            >
              <ChevronLeft className="h-5 w-5" />
            </button>
            <button
              type="button"
              aria-label="Ảnh sau"
              className="absolute right-3 top-1/2 -translate-y-1/2 rounded-full bg-white/90 p-2 text-slate-800 shadow-md transition-opacity hover:bg-white focus-visible:opacity-100 md:opacity-0 md:group-hover:opacity-100 md:group-focus-within:opacity-100"
              onClick={goToNext}
            >
              <ChevronRight className="h-5 w-5" />
            </button>
          </>
        )}

        <span className="absolute bottom-3 right-3 flex items-center gap-1.5 rounded-full bg-black/60 px-3 py-1 text-xs font-medium text-white">
          <Images className="h-3.5 w-3.5" />
          {mainImageIndex + 1} / {imageList.length}
        </span>
      </div>

      {imageList.length > 1 && (
        <div className="flex gap-2 overflow-x-auto pb-1 [scrollbar-width:thin]">
          {imageList.map((_, index) => (
            <button
              key={index}
              type="button"
              onClick={() => setMainImageIndex(index)}
              aria-label={`Xem ảnh ${index + 1}`}
              aria-current={mainImageIndex === index}
              className={cn(
                'relative h-16 w-24 flex-shrink-0 overflow-hidden rounded-lg transition-[opacity,box-shadow] md:h-20 md:w-28',
                mainImageIndex === index ? 'ring-2 ring-primary ring-offset-2 ring-offset-background' : 'opacity-70 hover:opacity-100',
              )}
            >
              <Image src={failedImageIndexes.has(index) ? PROPERTY_IMAGE_FALLBACK : previewList[index]} alt="" fill
                className="object-cover" onError={() => markImageFailed(index)} />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
