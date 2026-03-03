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

// Canonicalized KaTeX inline style declarations, for example:
// `height:1.3648em;vertical-align:-0.3558em;`
const KATEX_INLINE_STYLE_PATTERN = /^(?:[a-z-]+:[a-z0-9#().,%-]+;)+$/i

const KATEX_ALLOWED_STYLE_PROPERTIES = new Set([
  "border-bottom-width",
  "color",
  "height",
  "margin-left",
  "margin-right",
  "min-width",
  "padding-left",
  "position",
  "top",
  "vertical-align",
])

const CSS_ZERO_PATTERN = /^0(?:\.0+)?(?:em|ex|px|rem|%)?$/
const CSS_UNSIGNED_LENGTH_PATTERN =
  /^(?:\d+(?:\.\d+)?|\.\d+)(?:em|ex|px|rem|%)$/
const CSS_SIGNED_LENGTH_PATTERN =
  /^-?(?:\d+(?:\.\d+)?|\.\d+)(?:em|ex|px|rem|%)$/
const CSS_COLOR_PATTERN =
  /^(?:#(?:[0-9a-f]{3}|[0-9a-f]{6}|[0-9a-f]{8})|var\(--[a-z0-9-]+\))$/i

const defaultAttributes = defaultSchema.attributes ?? {}
const defaultTagNames = defaultSchema.tagNames ?? []

type HastNode = {
  type: string
  tagName?: string
  children?: HastNode[]
  properties?: Record<string, unknown>
  position?: {
    start?: unknown
    end?: unknown
  } | null
}

function getClassTokens(node: HastNode): string[] {
  if (!node.properties) {
    return []
  }

  const className = node.properties.className
  if (typeof className === "string") {
    return className
      .split(/\s+/)
      .map((token) => token.trim())
      .filter(Boolean)
  }

  if (Array.isArray(className)) {
    return className.flatMap((value) =>
      typeof value === "string"
        ? value
            .split(/\s+/)
            .map((token) => token.trim())
            .filter(Boolean)
        : []
    )
  }

  return []
}

function hasClassToken(node: HastNode, expectedToken: string): boolean {
  return getClassTokens(node).includes(expectedToken)
}

function hasKatexClass(node: HastNode): boolean {
  return getClassTokens(node).some(
    (token) =>
      token === "katex" ||
      token === "katex-display" ||
      token === "katex-error" ||
      token.startsWith("katex-")
  )
}

function hasSourcePosition(node: HastNode): boolean {
  return Boolean(node.position)
}

function isElementWithClass(node: HastNode, classToken: string): boolean {
  return node.type === "element" && hasClassToken(node, classToken)
}

function hasExpectedKatexChildren(node: HastNode): boolean {
  const children = node.children ?? []
  const hasMathmlChild = children.some((child) =>
    isElementWithClass(child, "katex-mathml")
  )
  const hasHtmlChild = children.some((child) =>
    isElementWithClass(child, "katex-html")
  )
  return hasMathmlChild && hasHtmlChild
}

function isTrustedKatexRootNode(node: HastNode): boolean {
  if (
    node.type !== "element" ||
    hasSourcePosition(node) ||
    !hasKatexClass(node)
  ) {
    return false
  }

  if (node.tagName === "span" && hasClassToken(node, "katex-error")) {
    return true
  }

  if (node.tagName === "span" && hasClassToken(node, "katex")) {
    return hasExpectedKatexChildren(node)
  }

  if (node.tagName === "span" && hasClassToken(node, "katex-display")) {
    return (node.children ?? []).some(
      (token) =>
        token.type === "element" &&
        token.tagName === "span" &&
        !hasSourcePosition(token) &&
        hasClassToken(token, "katex") &&
        hasExpectedKatexChildren(token)
    )
  }

  return false
}

function normalizeStyleValue(value: string): string {
  return value.trim().toLowerCase()
}

function isUnsignedCssLength(value: string): boolean {
  return CSS_ZERO_PATTERN.test(value) || CSS_UNSIGNED_LENGTH_PATTERN.test(value)
}

function isSignedCssLength(value: string): boolean {
  return CSS_ZERO_PATTERN.test(value) || CSS_SIGNED_LENGTH_PATTERN.test(value)
}

function isAllowedKatexStyleDeclaration(
  property: string,
  value: string
): boolean {
  if (!KATEX_ALLOWED_STYLE_PROPERTIES.has(property)) {
    return false
  }

  switch (property) {
    case "height":
    case "min-width":
    case "padding-left":
    case "border-bottom-width":
      return isUnsignedCssLength(value)
    case "margin-left":
    case "margin-right":
    case "top":
    case "vertical-align":
      return isSignedCssLength(value)
    case "position":
      return value === "relative"
    case "color":
      return CSS_COLOR_PATTERN.test(value)
    default:
      return false
  }
}

function sanitizeKatexInlineStyle(style: unknown): string | null {
  if (typeof style !== "string") {
    return null
  }

  const declarations: string[] = []
  for (const rawDeclaration of style.split(";")) {
    const declaration = rawDeclaration.trim()
    if (!declaration) {
      continue
    }

    const separatorIndex = declaration.indexOf(":")
    if (separatorIndex === -1) {
      continue
    }

    const property = declaration.slice(0, separatorIndex).trim().toLowerCase()
    const value = normalizeStyleValue(declaration.slice(separatorIndex + 1))
    if (!property || !value) {
      continue
    }

    if (!isAllowedKatexStyleDeclaration(property, value)) {
      continue
    }

    declarations.push(`${property}:${value}`)
  }

  if (declarations.length === 0) {
    return null
  }

  return `${declarations.join(";")};`
}

function sanitizeElementStyle(
  node: HastNode,
  inTrustedKatexSubtree: boolean
): void {
  if (!node.properties || !("style" in node.properties)) {
    return
  }

  if (!inTrustedKatexSubtree) {
    delete node.properties.style
    return
  }

  if (node.tagName !== "span" && node.tagName !== "svg") {
    delete node.properties.style
    return
  }

  const sanitizedStyle = sanitizeKatexInlineStyle(node.properties.style)
  if (sanitizedStyle) {
    node.properties.style = sanitizedStyle
  } else {
    delete node.properties.style
  }
}

function stripStylesOutsideKatexSubtree(
  node: HastNode,
  inTrustedKatexSubtree: boolean
) {
  const isElement = node.type === "element"
  const isTrustedKatexRoot = isElement && isTrustedKatexRootNode(node)
  const nextInTrustedKatexSubtree = inTrustedKatexSubtree || isTrustedKatexRoot

  if (isElement) {
    sanitizeElementStyle(node, nextInTrustedKatexSubtree)
  }

  for (const child of node.children ?? []) {
    stripStylesOutsideKatexSubtree(child, nextInTrustedKatexSubtree)
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
