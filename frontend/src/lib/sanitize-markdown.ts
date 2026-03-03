import DOMPurify from "dompurify"

const FORBIDDEN_ATTRIBUTES = ["style"]

export const SAFE_MARKDOWN_LINK_PREFIXES = [
  "http://",
  "https://",
  "mailto:",
  "tel:",
  "/",
  "#",
]

export const SAFE_MARKDOWN_IMAGE_PREFIXES = [
  "http://",
  "https://",
  "data:image/",
  "/",
]

export function sanitizeMarkdownContent(content: string): string {
  return DOMPurify.sanitize(content, {
    FORBID_ATTR: FORBIDDEN_ATTRIBUTES,
  })
}
