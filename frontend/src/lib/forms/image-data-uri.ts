const IMAGE_DATA_URI_PATTERN =
  /^data:image\/(png|jpeg|webp);base64,([A-Za-z0-9+/]+={0,2})$/

/** Largest decoded image the server stores, in bytes. */
export const MAX_FORM_IMAGE_BYTES = 100 * 1024

// Base64 encodes 3 bytes as 4 characters.
const MAX_ENCODED_LENGTH = Math.ceil(MAX_FORM_IMAGE_BYTES / 3) * 4

const MAGIC_CHECKS: Record<string, (head: string) => boolean> = {
  png: (head) => head.startsWith("\x89PNG\r\n\x1a\n"),
  jpeg: (head) => head.startsWith("\xff\xd8\xff"),
  webp: (head) => head.startsWith("RIFF") && head.slice(8, 12) === "WEBP",
}

/**
 * Whether a value is an inline image a form may render.
 *
 * Accepts only `data:image/(png|jpeg|webp);base64,` URIs within the server's
 * size limit whose first bytes match the declared type. Everything else,
 * including SVG and any remote URL, is refused, so a form never makes the
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
