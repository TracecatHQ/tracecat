import { parse, stringify } from "yaml"

export interface ParsedMarkdownFrontmatter {
  body: string
  data: Record<string, unknown>
  description?: string
  raw: string
  title?: string
}

export interface SplitMarkdownFrontmatter {
  body: string
  frontmatter: string
}

const FRONTMATTER_PATTERN =
  /^(?:\uFEFF)?(?:[ \t]*\r?\n)*---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/

/**
 * Splits a markdown document into a leading frontmatter block and body.
 */
export function splitMarkdownFrontmatter(
  markdown: string
): SplitMarkdownFrontmatter | null {
  const match = markdown.match(FRONTMATTER_PATTERN)
  if (!match) {
    return null
  }

  return {
    frontmatter: match[1],
    body: markdown.slice(match[0].length),
  }
}

/**
 * Recombines raw frontmatter text with a markdown body.
 */
export function composeMarkdownFrontmatter(
  frontmatter: string,
  body: string
): string {
  const lineBreak =
    frontmatter.includes("\r\n") || body.includes("\r\n") ? "\r\n" : "\n"
  const closingFence = `${lineBreak}---`
  const bodyPrefix =
    body.length === 0
      ? lineBreak
      : body.startsWith("\n") || body.startsWith("\r\n")
        ? ""
        : `${lineBreak}${lineBreak}`

  return `---${lineBreak}${frontmatter}${closingFence}${bodyPrefix}${body}`
}

/**
 * Extracts YAML frontmatter from the beginning of a markdown document.
 */
export function extractMarkdownFrontmatter(
  markdown: string
): ParsedMarkdownFrontmatter | null {
  const split = splitMarkdownFrontmatter(markdown)
  if (!split) {
    return null
  }

  const rawFrontmatter = split.frontmatter

  try {
    const parsed = parse(rawFrontmatter)
    if (!isRecord(parsed)) {
      return null
    }

    const title = normalizeOptionalText(parsed.title)
    const description = normalizeOptionalText(parsed.description)

    return {
      body: stripLeadingDuplicateTitleHeading(split.body, title),
      data: parsed,
      description,
      raw: rawFrontmatter.trim(),
      title,
    }
  } catch {
    return null
  }
}

/**
 * Formats a frontmatter key into a readable label.
 */
export function formatFrontmatterLabel(key: string): string {
  const normalized = key.replace(/[_-]+/g, " ").trim()
  if (!normalized) {
    return key
  }

  return normalized.charAt(0).toUpperCase() + normalized.slice(1)
}

/**
 * Serializes a frontmatter value for compact display when it is not a scalar.
 */
export function stringifyFrontmatterValue(value: unknown): string {
  try {
    const yamlValue = stringify(value).trim()
    if (yamlValue) {
      return yamlValue
    }
  } catch {
    // Fall back to JSON when the YAML serializer rejects the value.
  }

  return JSON.stringify(value, null, 2) ?? String(value)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function normalizeOptionalText(value: unknown): string | undefined {
  if (typeof value !== "string") {
    return undefined
  }

  const trimmed = value.trim()
  return trimmed ? trimmed : undefined
}

function stripLeadingDuplicateTitleHeading(
  markdown: string,
  title?: string
): string {
  if (!title) {
    return markdown
  }

  const withoutLeadingBlankLines = markdown.replace(
    /^(?:\uFEFF)?(?:[ \t]*\r?\n)*/,
    ""
  )
  const lineBreakIndex = withoutLeadingBlankLines.search(/\r?\n/)
  const firstLine =
    lineBreakIndex === -1
      ? withoutLeadingBlankLines
      : withoutLeadingBlankLines.slice(0, lineBreakIndex)

  if (!firstLine.startsWith("#")) {
    return markdown
  }

  const headingText = firstLine.replace(/^#\s+/, "").replace(/\s+#+\s*$/, "")
  if (normalizeHeading(headingText) !== normalizeHeading(title)) {
    return markdown
  }

  const remainder =
    lineBreakIndex === -1 ? "" : withoutLeadingBlankLines.slice(lineBreakIndex)

  return remainder.replace(/^(?:\r?\n\s*)+/, "")
}

function normalizeHeading(value: string): string {
  return value
    .trim()
    .replace(/\s+/g, " ")
    .replace(/\s+#+$/, "")
    .trim()
}
