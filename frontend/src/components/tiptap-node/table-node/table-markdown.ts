import { renderTableToMarkdown } from "@tiptap/extension-table"
// `@tiptap/core` is only a transitive dependency here; the repo reaches its
// types through `@tiptap/react`, which re-exports them.
import type { JSONContent, MarkdownRendererHelpers } from "@tiptap/react"
import {
  colwidthIsExplicit,
  readSpan,
} from "@/components/tiptap-node/table-node/table-column-widths"

/**
 * Serialization contract for tables in a case description.
 *
 * Case descriptions round-trip as Markdown, and upstream's GFM pipe renderer
 * never looks at cell attributes, so `colwidth` is dropped on every save. A
 * pipe table simply cannot carry column widths.
 *
 * So there are two shapes a table can be written in, and exactly one thing
 * moves a table between them:
 *
 * - Tables written by agents, workflows, or anything else that produces
 *   Markdown stay pure GFM pipe tables forever. Nothing here promotes them.
 * - A human dragging a resize handle materialises the on-screen widths into
 *   `colwidth` (see `table-materialize-widths.ts`), and from that point the
 *   table is written as a raw HTML block so the widths survive the round trip.
 * - If an agent later rewrites a resized table, it comes back as Markdown and
 *   the widths are silently dropped. That is safe: a table with no explicit
 *   widths falls back to the derived proportional widths in
 *   `table-column-widths.ts`, which is where it started.
 *
 * Content always outranks widths. Every failure path in this module falls back
 * to the pipe renderer rather than risking a mangled table.
 */

/** Marks that can be expressed as a plain inline HTML tag. */
const MARK_TAGS: Readonly<Record<string, string>> = {
  bold: "strong",
  italic: "em",
  strike: "s",
  underline: "u",
  code: "code",
}

/**
 * A blank line terminates a raw HTML block, so `marked` would stop treating the
 * rest of the table as HTML and start lexing it as Markdown.
 */
const BLANK_LINE = /\n[ \t]*\n/

/**
 * Render a table node to Markdown, preserving column widths when it has them.
 *
 * Falls back to upstream's GFM pipe renderer whenever the table carries no
 * explicit widths, or whenever its content cannot be expressed as a single
 * blank-line-free HTML block.
 */
export function renderTableMarkdown(
  node: JSONContent,
  helpers: MarkdownRendererHelpers
): string {
  if (!tableJsonHasExplicitWidths(node)) {
    return renderTableToMarkdown(node, helpers)
  }

  const html = renderTableToHtml(node)
  if (html === null) {
    return renderTableToMarkdown(node, helpers)
  }
  return html
}

/**
 * Whether a table in ProseMirror JSON form carries author-provided widths.
 *
 * The JSON twin of `tableNodeHasExplicitWidths`. `renderMarkdown` is handed
 * plain JSON rather than ProseMirror nodes, and the two shapes have no common
 * accessor worth abstracting over, so only the traversal is duplicated: both
 * decide through the shared `colwidthIsExplicit`.
 */
export function tableJsonHasExplicitWidths(table: JSONContent): boolean {
  for (const row of table.content ?? []) {
    for (const cell of row.content ?? []) {
      if (colwidthIsExplicit(cell.attrs?.colwidth)) {
        return true
      }
    }
  }
  return false
}

/**
 * Build the raw HTML block for a table, or `null` when it cannot be built.
 *
 * The output has no leading or trailing newline: the document joins its
 * top-level children with a blank line, which is what closes the HTML block.
 */
function renderTableToHtml(table: JSONContent): string | null {
  const rows = table.content
  if (!rows || rows.length === 0) {
    return null
  }

  const renderedRows: string[] = []
  for (const row of rows) {
    const rendered = renderRow(row)
    if (rendered === null) {
      return null
    }
    renderedRows.push(rendered)
  }

  const html = [
    "<table>",
    renderColgroup(rows[0]),
    ...renderedRows,
    "</table>",
  ].join("\n")

  if (BLANK_LINE.test(html)) {
    return null
  }
  return html
}

function renderRow(row: JSONContent): string | null {
  if (row.type !== "tableRow") {
    return null
  }
  const cells = row.content
  if (!cells || cells.length === 0) {
    return null
  }

  const rendered: string[] = []
  for (const cell of cells) {
    const html = renderCell(cell)
    if (html === null) {
      return null
    }
    rendered.push(html)
  }
  return `<tr>${rendered.join("")}</tr>`
}

/**
 * Render one cell, carrying its width on the cell itself.
 *
 * `colwidth` has to live on every `th` and `td`: upstream's `colwidth`
 * `parseHTML` reads the attribute directly, and its `colgroup` fallback is
 * unusable — `TableHeader` has none at all, and `TableCell` guards its own with
 * a truthiness check on the cell index, so column 0 is always dropped. The
 * `colgroup` this module emits is decorative, for renderers such as GitHub.
 */
function renderCell(cell: JSONContent): string | null {
  const tag = cellTag(cell.type)
  if (tag === null) {
    return null
  }

  const content = renderBlocks(cell.content)
  if (content === null) {
    return null
  }

  const attributes: string[] = []
  const colspan = readSpan(cell.attrs?.colspan)
  const rowspan = readSpan(cell.attrs?.rowspan)
  if (colspan > 1) {
    attributes.push(`colspan="${colspan}"`)
  }
  if (rowspan > 1) {
    attributes.push(`rowspan="${rowspan}"`)
  }
  const colwidth = readColwidth(cell.attrs?.colwidth)
  if (colwidth) {
    attributes.push(`colwidth="${colwidth.join(",")}"`)
  }

  const prefix = attributes.length > 0 ? ` ${attributes.join(" ")}` : ""
  return `<${tag}${prefix}>${content}</${tag}>`
}

function cellTag(type: string | undefined): "th" | "td" | null {
  if (type === "tableHeader") {
    return "th"
  }
  if (type === "tableCell") {
    return "td"
  }
  return null
}

/**
 * Emit a `<colgroup>` derived from the first row, matching upstream's
 * `createColGroup`. Purely decorative — see {@link renderCell}.
 */
function renderColgroup(firstRow: JSONContent): string {
  const cols: string[] = []
  for (const cell of firstRow.content ?? []) {
    const colspan = readSpan(cell.attrs?.colspan)
    const colwidth = readColwidth(cell.attrs?.colwidth)
    for (let index = 0; index < colspan; index += 1) {
      const width = colwidth?.[index]
      cols.push(width && width > 0 ? `<col width="${width}">` : "<col>")
    }
  }
  return `<colgroup>${cols.join("")}</colgroup>`
}

function renderBlocks(nodes: JSONContent[] | undefined): string | null {
  if (!nodes || nodes.length === 0) {
    return ""
  }

  const parts: string[] = []
  for (const node of nodes) {
    const rendered = renderBlock(node)
    if (rendered === null) {
      return null
    }
    parts.push(rendered)
  }
  return parts.join("")
}

/**
 * Render a block-level node inside a cell.
 *
 * Only the blocks that are actually reachable inside a table cell are handled.
 * Anything else — code blocks, Mermaid diagrams, nested tables, images,
 * horizontal rules, task lists — returns `null` so the caller falls back to the
 * pipe renderer. Their content can contain blank lines, which would break the
 * HTML block apart, and no Markdown syntax may be emitted inside a raw HTML
 * block because `marked` does not re-lex it.
 */
function renderBlock(node: JSONContent): string | null {
  switch (node.type) {
    case "paragraph": {
      const inline = renderInline(node.content)
      return inline === null ? null : `<p>${inline}</p>`
    }
    case "heading": {
      const inline = renderInline(node.content)
      if (inline === null) {
        return null
      }
      const level = readHeadingLevel(node.attrs?.level)
      return `<h${level}>${inline}</h${level}>`
    }
    case "bulletList":
      return renderList(node, "ul")
    case "orderedList":
      return renderList(node, "ol")
    default:
      return null
  }
}

function renderList(list: JSONContent, tag: "ul" | "ol"): string | null {
  const items: string[] = []
  for (const item of list.content ?? []) {
    if (item.type !== "listItem") {
      return null
    }
    const content = renderBlocks(item.content)
    if (content === null) {
      return null
    }
    items.push(`<li>${content}</li>`)
  }
  return `<${tag}>${items.join("")}</${tag}>`
}

function renderInline(nodes: JSONContent[] | undefined): string | null {
  if (!nodes || nodes.length === 0) {
    return ""
  }

  const parts: string[] = []
  for (const node of nodes) {
    if (node.type === "hardBreak") {
      parts.push("<br>")
      continue
    }
    if (node.type !== "text") {
      return null
    }
    const rendered = applyMarks(escapeHtml(node.text ?? ""), node.marks)
    if (rendered === null) {
      return null
    }
    parts.push(rendered)
  }
  return parts.join("")
}

/**
 * Wrap rendered text in its marks, or `null` for a mark with no HTML form.
 *
 * Bailing on an unrecognised mark is deliberate: falling back to the pipe
 * renderer costs the column widths, whereas dropping a mark would silently
 * change the document.
 */
function applyMarks(text: string, marks: JSONContent["marks"]): string | null {
  let rendered = text
  for (const mark of marks ?? []) {
    if (mark.type === "link") {
      const href = mark.attrs?.href
      if (typeof href !== "string") {
        return null
      }
      rendered = `<a href="${escapeHtml(href)}">${rendered}</a>`
      continue
    }
    const tag = MARK_TAGS[mark.type]
    if (!tag) {
      return null
    }
    rendered = `<${tag}>${rendered}</${tag}>`
  }
  return rendered
}

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
}

function readColwidth(value: unknown): number[] | null {
  if (!Array.isArray(value) || value.length === 0) {
    return null
  }
  const widths: number[] = []
  for (const width of value) {
    if (typeof width !== "number" || !Number.isFinite(width) || width < 0) {
      return null
    }
    widths.push(Math.round(width))
  }
  return widths
}

function readHeadingLevel(value: unknown): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return 1
  }
  return Math.min(Math.max(Math.round(value), 1), 6)
}
