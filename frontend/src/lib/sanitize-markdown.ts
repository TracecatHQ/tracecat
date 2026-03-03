import rehypeSanitize, { defaultSchema } from "rehype-sanitize"

const KATEX_TAG_NAMES = [
  "annotation",
  "g",
  "math",
  "merror",
  "mfrac",
  "mi",
  "mlabeledtr",
  "mmultiscripts",
  "mn",
  "mo",
  "mover",
  "mpadded",
  "mprescripts",
  "mroot",
  "mrow",
  "ms",
  "mspace",
  "msqrt",
  "mstyle",
  "msub",
  "msubsup",
  "msup",
  "mtable",
  "mtd",
  "mtext",
  "mtr",
  "munder",
  "munderover",
  "none",
  "path",
  "semantics",
  "svg",
]

const KATEX_INLINE_STYLE_PATTERN = /^[a-z0-9.%(),:;#\-\s]+$/i

const defaultAttributes = defaultSchema.attributes ?? {}
const defaultTagNames = defaultSchema.tagNames ?? []

type HastNode = {
  type: string
  children?: HastNode[]
  properties?: Record<string, unknown>
}

function hasKatexClass(node: HastNode): boolean {
  if (!node.properties) {
    return false
  }

  const className = node.properties.className
  if (typeof className === "string") {
    return (
      className === "katex" ||
      className === "katex-display" ||
      className.startsWith("katex-")
    )
  }

  if (Array.isArray(className)) {
    return className.some(
      (token) =>
        typeof token === "string" &&
        (token === "katex" ||
          token === "katex-display" ||
          token.startsWith("katex-"))
    )
  }

  return false
}

function stripStylesOutsideKatexSubtree(
  node: HastNode,
  inKatexSubtree: boolean
) {
  const isElement = node.type === "element"
  const isKatexNode = isElement && hasKatexClass(node)
  const nextInKatexSubtree = inKatexSubtree || isKatexNode

  if (isElement && !nextInKatexSubtree && node.properties) {
    delete node.properties.style
  }

  for (const child of node.children ?? []) {
    stripStylesOutsideKatexSubtree(child, nextInKatexSubtree)
  }
}

function stripStylesOutsideKatex() {
  return (tree: HastNode) => {
    stripStylesOutsideKatexSubtree(tree, false)
  }
}

const sanitizeSchema = {
  ...defaultSchema,
  tagNames: Array.from(new Set([...defaultTagNames, ...KATEX_TAG_NAMES])),
  attributes: {
    ...defaultAttributes,
    "*": [...(defaultAttributes["*"] ?? []), "className", "id"],
    a: [...(defaultAttributes.a ?? []), "target", "rel", "title"],
    annotation: [...(defaultAttributes.annotation ?? []), "encoding"],
    code: [...(defaultAttributes.code ?? []), "className"],
    div: [...(defaultAttributes.div ?? []), "className", "id"],
    math: [...(defaultAttributes.math ?? []), "xmlns"],
    mi: [...(defaultAttributes.mi ?? []), "mathvariant"],
    mo: [...(defaultAttributes.mo ?? []), "fence", "stretchy"],
    mover: [...(defaultAttributes.mover ?? []), "accent"],
    mstyle: [
      ...(defaultAttributes.mstyle ?? []),
      "displaystyle",
      "mathcolor",
      "scriptlevel",
    ],
    mtable: [
      ...(defaultAttributes.mtable ?? []),
      "columnalign",
      "columnspacing",
      "rowspacing",
    ],
    path: [...(defaultAttributes.path ?? []), "d"],
    pre: [...(defaultAttributes.pre ?? []), "className"],
    span: [
      ...(defaultAttributes.span ?? []),
      "className",
      ["style", KATEX_INLINE_STYLE_PATTERN],
    ],
    svg: [
      ...(defaultAttributes.svg ?? []),
      "height",
      "preserveAspectRatio",
      ["style", KATEX_INLINE_STYLE_PATTERN],
      "viewBox",
      "width",
      "xmlns",
    ],
  },
}

export const ALLOWED_MARKDOWN_LINK_PREFIXES = ["*"]

export const ALLOWED_MARKDOWN_IMAGE_PREFIXES = ["*"]

// Used to resolve path-relative markdown URLs during URL hardening.
export const DEFAULT_MARKDOWN_ORIGIN = "https://tracecat.local"

export function getStreamdownRehypePlugins() {
  // Streamdown prepends rehype-katex before user plugins.
  // Strip inline styles outside KaTeX output, then sanitize with a KaTeX-aware schema.
  return [stripStylesOutsideKatex, [rehypeSanitize, sanitizeSchema]]
}
