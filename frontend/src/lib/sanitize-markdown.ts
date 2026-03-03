import rehypeKatex from "rehype-katex"
import rehypeSanitize, { defaultSchema } from "rehype-sanitize"

const defaultAttributes = defaultSchema.attributes ?? {}

const sanitizeSchema = {
  ...defaultSchema,
  attributes: {
    ...defaultAttributes,
    "*": [...(defaultAttributes["*"] ?? []), "className", "id"],
    a: [...(defaultAttributes.a ?? []), "target", "rel", "title"],
    code: [...(defaultAttributes.code ?? []), "className"],
    div: [...(defaultAttributes.div ?? []), "className", "id"],
    pre: [...(defaultAttributes.pre ?? []), "className"],
    span: [...(defaultAttributes.span ?? []), "className"],
  },
}

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

export function getStreamdownRehypePlugins() {
  // Sanitize first, then render KaTeX to avoid stripping math markup
  return [[rehypeSanitize, sanitizeSchema], rehypeKatex]
}
