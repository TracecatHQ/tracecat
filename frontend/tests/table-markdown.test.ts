import { renderTableToMarkdown } from "@tiptap/extension-table"
import { TableCell } from "@tiptap/extension-table-cell"
import { TableHeader } from "@tiptap/extension-table-header"
import { TableRow } from "@tiptap/extension-table-row"
import { MarkdownManager } from "@tiptap/markdown"
import type { JSONContent, MarkdownRendererHelpers } from "@tiptap/react"
import { generateJSON } from "@tiptap/react"
import { StarterKit } from "@tiptap/starter-kit"
import {
  renderTableMarkdown,
  tableJsonHasExplicitWidths,
} from "@/components/tiptap-node/table-node/table-markdown"
import {
  createTracecatTable,
  TracecatTable,
} from "@/components/tiptap-node/table-node/table-node-extension"

/** Width persistence as the case description enables it. */
const PERSIST = { persistColumnWidths: true } as const

/** Width persistence as every other surface leaves it. */
const NO_PERSIST = { persistColumnWidths: false } as const

/** The extension set a case description round-trips through. */
const extensions = [
  StarterKit,
  createTracecatTable(PERSIST),
  TableRow,
  TableHeader,
  TableCell,
]

/** The same set for a surface that never persists widths. */
const pipeOnlyExtensions = [
  StarterKit,
  TracecatTable,
  TableRow,
  TableHeader,
  TableCell,
]

/**
 * Minimal stand-in for the renderer helpers `@tiptap/markdown` passes in.
 *
 * `renderTableToMarkdown` only ever calls `renderChildren`, and only to flatten
 * a cell to text, so a plain text walk is a faithful substitute.
 */
const helpers: MarkdownRendererHelpers = {
  renderChildren: (nodes) => renderPlainText(nodes),
  wrapInBlock: (prefix, content) => `${prefix}${content}`,
  indent: (content) => content,
}

function renderPlainText(nodes: JSONContent | JSONContent[]): string {
  if (Array.isArray(nodes)) {
    return nodes.map(renderPlainText).join("")
  }
  if (nodes.type === "text") {
    return nodes.text ?? ""
  }
  return renderPlainText(nodes.content ?? [])
}

function makeCell(
  type: "tableHeader" | "tableCell",
  text: string,
  colwidth: number[] | null
): JSONContent {
  return {
    type,
    attrs: { colspan: 1, rowspan: 1, colwidth },
    content: [
      {
        type: "paragraph",
        content: text.length > 0 ? [{ type: "text", text }] : [],
      },
    ],
  }
}

/**
 * Build a table whose first row is a header row. `widths` is applied to every
 * cell in the matching column, as materialisation does; `null` leaves the table
 * width-less, as anything produced from Markdown is.
 */
function makeTable(rows: string[][], widths: number[] | null): JSONContent {
  return {
    type: "table",
    content: rows.map((cells, rowIndex) => ({
      type: "tableRow",
      content: cells.map((text, colIndex) =>
        makeCell(
          rowIndex === 0 ? "tableHeader" : "tableCell",
          text,
          widths ? [widths[colIndex]] : null
        )
      ),
    })),
  }
}

function openingCellTags(html: string): string[] {
  return html.match(/<(?:th|td)\b[^>]*>/g) ?? []
}

/** A table with explicit widths whose first cell holds `blocks`. */
function makeTableHolding(blocks: JSONContent[]): JSONContent {
  const table = makeTable([["Alpha", "Beta"]], [120, 240])
  const cell = table.content?.[0]?.content?.[0]
  if (!cell) {
    throw new Error("no cell to fill")
  }
  cell.content = blocks
  return table
}

/** A one-item list of the given type, carrying `attrs` verbatim. */
function makeList(
  type: "orderedList" | "bulletList",
  attrs: JSONContent["attrs"]
): JSONContent {
  return {
    type,
    attrs,
    content: [
      {
        type: "listItem",
        content: [
          { type: "paragraph", content: [{ type: "text", text: "one" }] },
        ],
      },
    ],
  }
}

/** Parse `markdown` back through `manager` and return the one table in it. */
function parseOnlyTable(
  manager: MarkdownManager,
  markdown: string
): JSONContent {
  const parsed = manager.parse(markdown)
  const table = parsed.content?.[0]
  expect(parsed.content).toHaveLength(1)
  expect(table?.type).toBe("table")
  return table as JSONContent
}

/**
 * Take rendered Markdown through the same lexer and parser a case description
 * does, and return the first block of the first cell that comes back.
 */
function roundTripFirstCellBlock(markdown: string): JSONContent | undefined {
  const manager = new MarkdownManager({
    extensions,
    markedOptions: { gfm: true },
  })
  const tokens = manager.instance
    .lexer(markdown)
    .filter((token) => token.type !== "space")
  expect(tokens).toHaveLength(1)
  expect(tokens[0].type).toBe("html")

  const parsed: JSONContent = generateJSON(
    (tokens[0] as { text: string }).text,
    extensions
  )
  return parsed.content?.[0]?.content?.[0]?.content?.[0]?.content?.[0]
}

describe("renderTableMarkdown without explicit widths, persistence enabled", () => {
  it("emits a pipe table byte-identical to the upstream renderer", () => {
    const table = makeTable(
      [
        ["Indicator", "Verdict"],
        ["1.2.3.4", "malicious"],
      ],
      null
    )

    const rendered = renderTableMarkdown(table, helpers, PERSIST)
    expect(rendered).toBe(renderTableToMarkdown(table, helpers))
    expect(rendered).toContain("| Indicator | Verdict   |")
    expect(rendered).not.toContain("<table>")
  })

  it("leaves a table whose only widths are colspan zero-padding alone", () => {
    const table = makeTable(
      [
        ["Indicator", "Verdict"],
        ["1.2.3.4", "malicious"],
      ],
      null
    )
    // prosemirror-tables seeds colwidth with zeroes for colspan cells; that is
    // not an author-provided width.
    const firstCell = table.content?.[0]?.content?.[0]
    if (firstCell?.attrs) {
      firstCell.attrs.colwidth = [0, 0]
    }

    expect(renderTableMarkdown(table, helpers, PERSIST)).toBe(
      renderTableToMarkdown(table, helpers)
    )
  })
})

describe("renderTableMarkdown with explicit widths, persistence enabled", () => {
  it("emits an HTML block carrying colwidth on every cell", () => {
    const rendered = renderTableMarkdown(
      makeTable(
        [
          ["Indicator", "Verdict"],
          ["1.2.3.4", "malicious"],
        ],
        [120, 240]
      ),
      helpers,
      PERSIST
    )

    expect(rendered.startsWith("<table>")).toBe(true)
    expect(rendered.endsWith("</table>")).toBe(true)
    // A blank line would end the raw HTML block and split the table apart.
    expect(rendered).not.toMatch(/\n[ \t]*\n/)

    const cellTags = openingCellTags(rendered)
    expect(cellTags).toHaveLength(4)
    for (const tag of cellTags) {
      expect(tag).toMatch(/colwidth="\d+"/)
    }
    // The colgroup is a courtesy for external renderers, never relied on.
    expect(rendered).toContain(
      '<colgroup><col width="120"><col width="240"></colgroup>'
    )
  })

  it("omits colspan and rowspan unless they are greater than one", () => {
    const table = makeTable([["Alpha", "Beta"]], [120, 240])
    const rendered = renderTableMarkdown(table, helpers, PERSIST)
    expect(rendered).not.toContain("colspan")
    expect(rendered).not.toContain("rowspan")

    const spanned = makeTable([["Alpha", "Beta"]], [120, 240])
    const cell = spanned.content?.[0]?.content?.[0]
    if (cell?.attrs) {
      cell.attrs.colspan = 2
      cell.attrs.rowspan = 3
      cell.attrs.colwidth = [120, 240]
    }
    const renderedSpanned = renderTableMarkdown(spanned, helpers, PERSIST)
    expect(renderedSpanned).toContain('colspan="2"')
    expect(renderedSpanned).toContain('rowspan="3"')
    expect(renderedSpanned).toContain('colwidth="120,240"')
  })

  it("escapes text and renders supported marks as HTML, never Markdown", () => {
    const table = makeTable([["Alpha", "Beta"]], [120, 240])
    const [first, second] = table.content?.[0]?.content ?? []
    if (first) {
      first.content = [
        {
          type: "paragraph",
          content: [{ type: "text", text: '5 < 6 & "ok"' }],
        },
      ]
    }
    if (second) {
      second.content = [
        {
          type: "paragraph",
          content: [
            { type: "text", text: "bold", marks: [{ type: "bold" }] },
            { type: "hardBreak" },
            {
              type: "text",
              text: "link",
              marks: [{ type: "link", attrs: { href: "https://example.com" } }],
            },
          ],
        },
      ]
    }

    const rendered = renderTableMarkdown(table, helpers, PERSIST)
    expect(rendered).toContain("5 &lt; 6 &amp; &quot;ok&quot;")
    expect(rendered).toContain("<strong>bold</strong>")
    expect(rendered).toContain("<br>")
    expect(rendered).toContain('<a href="https://example.com">link</a>')
    expect(rendered).not.toContain("**")
  })

  it("falls back to a pipe table when a cell holds a code block", () => {
    const table = makeTable(
      [
        ["Indicator", "Verdict"],
        ["1.2.3.4", "malicious"],
      ],
      [120, 240]
    )
    const cell = table.content?.[1]?.content?.[0]
    if (cell) {
      cell.content = [
        {
          type: "codeBlock",
          attrs: { language: null },
          content: [{ type: "text", text: "first\n\nsecond" }],
        },
      ]
    }

    const rendered = renderTableMarkdown(table, helpers, PERSIST)
    expect(rendered).toBe(renderTableToMarkdown(table, helpers))
    expect(rendered).not.toContain("<table>")
  })

  it("falls back to a pipe table for a mark it cannot express", () => {
    const table = makeTable([["Alpha", "Beta"]], [120, 240])
    const cell = table.content?.[0]?.content?.[0]
    if (cell) {
      cell.content = [
        {
          type: "paragraph",
          content: [
            { type: "text", text: "hi", marks: [{ type: "superscript" }] },
          ],
        },
      ]
    }

    expect(renderTableMarkdown(table, helpers, PERSIST)).toBe(
      renderTableToMarkdown(table, helpers)
    )
  })
})

describe("renderTableMarkdown with explicit widths, persistence disabled", () => {
  it("emits a pipe table and simply loses the widths", () => {
    const table = makeTable(
      [
        ["Indicator", "Verdict"],
        ["1.2.3.4", "malicious"],
      ],
      [120, 240]
    )

    const rendered = renderTableMarkdown(table, helpers, NO_PERSIST)

    // No HTML anywhere: a workflow README or a case comment has to stay
    // Markdown whatever the user dragged, so the widths are what gives.
    expect(rendered).not.toContain("<table>")
    expect(rendered).not.toContain("colwidth")
    expect(rendered).toBe(renderTableToMarkdown(table, helpers))
    expect(rendered).toContain("| Indicator | Verdict   |")
  })

  it("is what an editor gets by default", () => {
    // `createTracecatTable()` is `SimpleEditor`'s extension with the prop left
    // off, and `TracecatTable` is the ready-made export every other surface
    // reaches for. Neither takes a flag, so a consumer that never heard of
    // width persistence cannot opt into HTML tables by accident.
    for (const table of [createTracecatTable(), TracecatTable]) {
      const manager = new MarkdownManager({
        extensions: [StarterKit, table, TableRow, TableHeader, TableCell],
        markedOptions: { gfm: true },
      })

      const resized = makeTable(
        [
          ["Indicator", "Verdict"],
          ["1.2.3.4", "malicious"],
        ],
        [140, 96]
      )
      // The table really is one a user has dragged; without this the test would
      // pass against a gate that was wide open.
      expect(tableJsonHasExplicitWidths(resized)).toBe(true)

      const markdown = manager.serialize({ type: "doc", content: [resized] })

      expect(markdown).not.toContain("<table>")
      expect(markdown).not.toContain("colwidth")
      expect(markdown).toContain("| Indicator | Verdict   |")

      // And the widths are gone for good, not merely unrendered. A pipe table
      // is parsed straight from Markdown tokens rather than through the schema,
      // so its cells come back with no `attrs` at all — hence testing for a
      // width that is absent rather than for an explicit null.
      const parsedTable = parseOnlyTable(manager, markdown)
      const widths = (parsedTable.content ?? []).flatMap((row: JSONContent) =>
        (row.content ?? []).map((cell: JSONContent) => cell.attrs?.colwidth)
      )
      expect(widths).toHaveLength(4)
      expect(widths.filter((width) => Array.isArray(width))).toEqual([])
      // The very question the serializer asks: nothing survives that could turn
      // the next save of this document into an HTML block.
      expect(tableJsonHasExplicitWidths(parsedTable)).toBe(false)
    }
  })

  it("keeps everything else about a table intact", () => {
    const manager = new MarkdownManager({
      extensions: pipeOnlyExtensions,
      markedOptions: { gfm: true },
    })

    const markdown = manager.serialize({
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text: "Before." }] },
        makeTable(
          [
            ["Indicator", "Verdict"],
            ["1.2.3.4", "malicious"],
          ],
          [140, 96]
        ),
        { type: "paragraph", content: [{ type: "text", text: "After." }] },
      ],
    })

    const parsed = manager.parse(markdown)
    expect(parsed.content?.map((node: JSONContent) => node.type)).toEqual([
      "paragraph",
      "table",
      "paragraph",
    ])
    const texts = (parsed.content?.[1]?.content ?? []).map((row: JSONContent) =>
      (row.content ?? []).map((cell: JSONContent) => renderPlainText(cell))
    )
    expect(texts).toEqual([
      ["Indicator", "Verdict"],
      ["1.2.3.4", "malicious"],
    ])
  })
})

describe("renderTableMarkdown list numbering", () => {
  it("writes the numbering a list does not start at 1 with", () => {
    const rendered = renderTableMarkdown(
      makeTableHolding([makeList("orderedList", { start: 3, type: null })]),
      helpers,
      PERSIST
    )
    expect(rendered).toContain('<ol start="3"><li><p>one</p></li></ol>')
  })

  it("writes the marker style an ordered list was given", () => {
    const rendered = renderTableMarkdown(
      makeTableHolding([makeList("orderedList", { start: 1, type: "a" })]),
      helpers,
      PERSIST
    )
    expect(rendered).toContain('<ol type="a">')
  })

  it("leaves an ordinary list bare, so existing content does not churn", () => {
    const rendered = renderTableMarkdown(
      makeTableHolding([makeList("orderedList", { start: 1, type: null })]),
      helpers,
      PERSIST
    )
    expect(rendered).toContain("<ol><li><p>one</p></li></ol>")

    // `BulletList` defines no attributes to carry in the first place.
    const bullets = renderTableMarkdown(
      makeTableHolding([makeList("bulletList", {})]),
      helpers,
      PERSIST
    )
    expect(bullets).toContain("<ul><li><p>one</p></li></ul>")
  })

  it("rehydrates the numbering after a full round trip", () => {
    const markdown = renderTableMarkdown(
      makeTableHolding([makeList("orderedList", { start: 3, type: "a" })]),
      helpers,
      PERSIST
    )

    const list = roundTripFirstCellBlock(markdown)
    expect(list?.type).toBe("orderedList")
    // Without the attributes on the tag, upstream's `parseHTML` defaults these
    // back to 1 and null, and a list that read `3. 4. 5.` comes back `1. 2. 3.`
    expect(list?.attrs?.start).toBe(3)
    expect(list?.attrs?.type).toBe("a")
  })
})

describe("renderTableMarkdown round trip", () => {
  it("survives the Markdown lexer and rehydrates widths on every cell", () => {
    const table = makeTable(
      [
        ["Indicator", "Verdict"],
        ["1.2.3.4", "malicious"],
        ["5.6.7.8", "benign"],
      ],
      [120, 240]
    )

    const markdown = renderTableMarkdown(table, helpers, PERSIST)

    // The whole table has to survive as a single raw HTML block; anything else
    // means the block was terminated early and the rest lexed as Markdown.
    const manager = new MarkdownManager({
      extensions,
      markedOptions: { gfm: true },
    })
    const tokens = manager.instance
      .lexer(markdown)
      .filter((token) => token.type !== "space")
    expect(tokens).toHaveLength(1)
    expect(tokens[0].type).toBe("html")
    expect((tokens[0] as { block?: boolean }).block).toBe(true)

    // This is what `@tiptap/markdown` does with a raw html token.
    const parsed = generateJSON(
      (tokens[0] as { text: string }).text,
      extensions
    )
    const parsedTable = parsed.content?.[0]
    expect(parsedTable?.type).toBe("table")

    const rows = parsedTable?.content ?? []
    expect(rows).toHaveLength(3)

    const widthsByRow = rows.map((row: JSONContent) =>
      (row.content ?? []).map((cell: JSONContent) => cell.attrs?.colwidth)
    )
    // Both header cells and column 0 of every body row are the exact pair of
    // upstream `colwidth` parse bugs this serializer works around:
    // `TableHeader` has no colgroup fallback at all, and `TableCell` guards its
    // fallback with a truthy cell index, so column 0 is always dropped. A
    // colgroup-only table would leave nulls here.
    expect(widthsByRow).toEqual([
      [[120], [240]],
      [[120], [240]],
      [[120], [240]],
    ])

    const cellTypes = rows.map((row: JSONContent) =>
      (row.content ?? []).map((cell: JSONContent) => cell.type)
    )
    expect(cellTypes[0]).toEqual(["tableHeader", "tableHeader"])
    expect(cellTypes[1]).toEqual(["tableCell", "tableCell"])

    const texts = rows.map((row: JSONContent) =>
      (row.content ?? []).map((cell: JSONContent) => renderPlainText(cell))
    )
    expect(texts).toEqual([
      ["Indicator", "Verdict"],
      ["1.2.3.4", "malicious"],
      ["5.6.7.8", "benign"],
    ])
  })

  it("keeps the block intact when the table sits between paragraphs", () => {
    const markdown = renderTableMarkdown(
      makeTable(
        [
          ["Indicator", "Verdict"],
          ["1.2.3.4", "malicious"],
        ],
        [120, 240]
      ),
      helpers,
      PERSIST
    )
    // The document joins top-level children with a blank line, which is what
    // opens and closes the HTML block.
    const document = ["Before the table.", markdown, "After the table."].join(
      "\n\n"
    )

    const manager = new MarkdownManager({
      extensions,
      markedOptions: { gfm: true },
    })
    const tokens = manager.instance
      .lexer(document)
      .filter((token) => token.type !== "space")
    expect(tokens.map((token) => token.type)).toEqual([
      "paragraph",
      "html",
      "paragraph",
    ])
    expect((tokens[1] as { text: string }).text.trim()).toBe(markdown)
  })

  it("survives the real serializer and parser a case description uses", () => {
    const manager = new MarkdownManager({
      extensions,
      markedOptions: { gfm: true },
    })

    const makeDocument = (widths: number[] | null): JSONContent => ({
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text: "Before." }] },
        makeTable(
          [
            ["Indicator", "Verdict"],
            ["1.2.3.4", "malicious"],
          ],
          widths
        ),
        { type: "paragraph", content: [{ type: "text", text: "After." }] },
      ],
    })

    // A table nobody has resized stays a pipe table, which is what agents and
    // workflows write and what they can read back.
    expect(manager.serialize(makeDocument(null))).toContain(
      "| Indicator | Verdict   |"
    )

    const markdown = manager.serialize(makeDocument([140, 96]))
    expect(markdown).toContain("\n\n<table>\n")
    expect(markdown).toContain("</table>\n\nAfter.")

    const parsed = manager.parse(markdown)
    expect(parsed.content?.map((node: JSONContent) => node.type)).toEqual([
      "paragraph",
      "table",
      "paragraph",
    ])
    const widths = (parsed.content?.[1]?.content ?? []).map(
      (row: JSONContent) =>
        (row.content ?? []).map((cell: JSONContent) => cell.attrs?.colwidth)
    )
    expect(widths).toEqual([
      [[140], [96]],
      [[140], [96]],
    ])
  })
})
