/**
 * Normalize an HTTP(S) origin using the same DNS-root-dot behavior as the
 * backend egress policy.
 */
export function normalizeHttpOrigin(value: string): string | null {
  try {
    const url = new URL(value.trim())
    if (url.protocol !== "http:" && url.protocol !== "https:") {
      return null
    }

    const hostname = url.hostname.replace(/[.]+$/, "")
    if (hostname === "") {
      return null
    }
    url.hostname = hostname
    return url.origin
  } catch {
    return null
  }
}
