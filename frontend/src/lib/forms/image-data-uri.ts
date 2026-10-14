const IMAGE_DATA_URI_PATTERN =
  /^data:image\/(png|jpeg|webp);base64,([A-Za-z0-9+/]+={0,2})$/

/** Largest decoded image the server stores, in bytes. */
const MAX_FORM_IMAGE_BYTES = 100 * 1024

// Base64 encodes 3 bytes as 4 characters.
const MAX_ENCODED_LENGTH = Math.ceil(MAX_FORM_IMAGE_BYTES / 3) * 4

const MAGIC_CHECKS: Record<string, (head: string) => boolean> = {
  png: (head) => head.startsWith("\x89PNG\r\n\x1a\n"),
  jpeg: (head) => head.startsWith("\xff\xd8\xff"),
  webp: (head) => head.startsWith("RIFF") && head.slice(8, 12) === "WEBP",
}

/**
 * Whether a value is an inline image a form may render: a PNG, JPEG or WebP
 * base64 `data:` URI within the server's size limit whose first bytes match
 * its type. SVG and remote URLs are refused, so a form never makes the
 * browser fetch an author-supplied address.
 */
export function isAllowedImageDataUri(value: unknown): value is string {
  if (typeof value !== "string") {
    return false
  }
  const match = IMAGE_DATA_URI_PATTERN.exec(value)
  if (!match) {
    return false
  }
  const [, subtype, encoded] = match
  if (encoded.length > MAX_ENCODED_LENGTH) {
    return false
  }
  try {
    // 16 characters decode to the 12 bytes the longest signature needs.
    return MAGIC_CHECKS[subtype](atob(encoded.slice(0, 16)))
  } catch {
    return false
  }
}

/** Image types a form config may hold. */
export const FORM_IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"]

/** Longest edge of an inserted image, in pixels. */
const FORM_IMAGE_MAX_EDGE = 320
const ENCODE_QUALITY = 0.85

/** Decoded size in bytes of a base64 `data:` URI. */
function getDataUriByteSize(dataUri: string): number {
  const encoded = dataUri.slice(dataUri.indexOf(",") + 1)
  const padding = encoded.match(/=*$/)?.[0].length ?? 0
  return Math.max(0, Math.floor((encoded.length * 3) / 4) - padding)
}

/**
 * Find every base64 image `data:` URI in a config text, of any image type:
 * the editor folds each one into a chip, and a type the server refuses still
 * has to be folded to be readable.
 */
export function findImageDataUris(
  text: string
): { from: number; to: number; bytes: number }[] {
  return [
    ...text.matchAll(/data:image\/[a-z0-9.+-]+;base64,[A-Za-z0-9+/]*={0,2}/g),
  ].map((match) => ({
    from: match.index,
    to: match.index + match[0].length,
    bytes: getDataUriByteSize(match[0]),
  }))
}

/** An image the author picked cannot be inserted. `message` is shown as is. */
export class FormImageError extends Error {}

/**
 * Draw an image onto a canvas no longer than {@link FORM_IMAGE_MAX_EDGE} on
 * its long edge, without scaling up, and re-encode it as WebP. A browser
 * without a WebP encoder answers with PNG; a photo is then encoded as JPEG,
 * because PNG would be far over the limit.
 */
async function resizeImageToDataUri(file: File): Promise<string> {
  const bitmap = await createImageBitmap(file)
  try {
    const scale = Math.min(
      1,
      FORM_IMAGE_MAX_EDGE / Math.max(bitmap.width, bitmap.height)
    )
    const canvas = document.createElement("canvas")
    canvas.width = Math.max(1, Math.round(bitmap.width * scale))
    canvas.height = Math.max(1, Math.round(bitmap.height * scale))
    const context = canvas.getContext("2d")
    if (!context) {
      throw new FormImageError("This browser can't resize images.")
    }
    context.drawImage(bitmap, 0, 0, canvas.width, canvas.height)
    const encoded = canvas.toDataURL("image/webp", ENCODE_QUALITY)
    if (encoded.startsWith("data:image/webp") || file.type === "image/png") {
      return encoded
    }
    return canvas.toDataURL("image/jpeg", ENCODE_QUALITY)
  } finally {
    bitmap.close()
  }
}

/**
 * Turn a picked PNG, JPEG or WebP file into a resized inline image the
 * server accepts.
 *
 * @throws FormImageError with a message for the author.
 */
export async function prepareFormImage(file: File): Promise<string> {
  if (!FORM_IMAGE_TYPES.includes(file.type)) {
    throw new FormImageError("Use a PNG, JPEG or WebP image.")
  }
  let dataUri: string
  try {
    dataUri = await resizeImageToDataUri(file)
  } catch (error) {
    if (error instanceof FormImageError) {
      throw error
    }
    throw new FormImageError("This file could not be read as an image.")
  }
  const bytes = getDataUriByteSize(dataUri)
  if (bytes > MAX_FORM_IMAGE_BYTES) {
    const kb = (size: number) => `${Math.ceil(size / 1024)} KB`
    throw new FormImageError(
      `This image is ${kb(bytes)} after resizing. The limit is ${kb(MAX_FORM_IMAGE_BYTES)}. Use a simpler image.`
    )
  }
  if (!isAllowedImageDataUri(dataUri)) {
    throw new FormImageError("This file could not be read as an image.")
  }
  return dataUri
}
