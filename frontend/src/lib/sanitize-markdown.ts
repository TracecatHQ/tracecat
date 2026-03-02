import DOMPurify from "dompurify"

const FORBIDDEN_ATTRIBUTES = ["style"]

export function sanitizeMarkdownContent(content: string): string {
  return DOMPurify.sanitize(content, {
    FORBID_ATTR: FORBIDDEN_ATTRIBUTES,
  })
}
