import { TableCell } from "@tiptap/extension-table-cell"
import { TableHeader } from "@tiptap/extension-table-header"
import { TableRow } from "@tiptap/extension-table-row"
import { MarkdownManager } from "@tiptap/markdown"
import { columnResizingPluginKey } from "@tiptap/pm/tables"
import type { JSONContent } from "@tiptap/react"
import { Editor } from "@tiptap/react"
import { StarterKit } from "@tiptap/starter-kit"
import { createTracecatTable } from "@/components/tiptap-node/table-node/table-node-extension"

/**
 * The extension set a case description round-trips through — the one surface
 * that persists dragged column widths, so the one where materialisation is
 * visible in the saved Markdown.
 */
const extensions = [
  StarterKit,
  createTracecatTable({ persistColumnWidths: true }).configure({
    resizable: true,
    cellMinWidth: 48,
    handleWidth: 6,
  }),
  TableRow,
  TableHeader,
  TableCell,
]

/**
 * A two-column table with no widths at all, so its columns are proportioned
 * from the cell text rather than from `colwidth`.
 */
const DERIVED_TABLE = `
<table>
  <tbody>
    <tr><th>Host</th><th>${"n".repeat(80)}</th></tr>
    <tr><td>web-1</td><td>${"note ".repeat(20)}</td></tr>
  </tbody>
</table>
`

/** The same shape, but already carrying explicit widths. */
const SIZED_TABLE = `
<table>
  <tbody>
    <tr><th colwidth="100">H1</th><th colwidth="300">H2</th></tr>
    <tr><td colwidth="100">a</td><td colwidth="300">b</td></tr>
  </tbody>
</table>
`

/**
 * A three-column table whose header merges the first two columns, so the only
 * cells measuring those two on their own are in the body row.
 *
 * Merged cells cannot be made from the table toolbar, which has no merge or
 * split buttons; this is what a table pasted as HTML looks like.
 */
const SPANNED_TABLE = `
<table>
  <tbody>
    <tr><th colspan="2">Host and note</th><th>Owner</th></tr>
    <tr><td>web-1</td><td>${"note ".repeat(20)}</td><td>ops</td></tr>
  </tbody>
</table>
`

/** The same, but with no row that measures the first two columns separately. */
const FULLY_SPANNED_TABLE = `
<table>
  <tbody>
    <tr><th colspan="2">Host and note</th><th>Owner</th></tr>
    <tr><td colspan="2">web-1</td><td>ops</td></tr>
  </tbody>
</table>
`

/** Rendered widths of the columns, as a browser would lay them out. */
const COLUMN_PIXEL_WIDTHS = [60, 240]

/** The same, for the three-column merged fixtures. */
const SPANNED_PIXEL_WIDTHS = [60, 240, 100]

/** Where the pointer goes down; only its distance to later events matters. */
const PRESS_X = 300

function createEditor(content: string): {
  editor: Editor
  element: HTMLElement
} {
  const element = document.createElement("div")
  document.body.appendChild(element)
  const editor = new Editor({ element, extensions, content })
  return { editor, element }
}

function withEditor(
  content: string,
  run: (editor: Editor, element: HTMLElement) => void
): void {
  const { editor, element } = createEditor(content)
  try {
    run(editor, element)
  } finally {
    // `columnResizing` keeps window-level listeners for as long as a drag is
    // held, and jsdom's window outlives the editor. A test that presses without
    // releasing would otherwise leave them to fire into a destroyed view.
    releasePointer(0)
    editor.destroy()
    element.remove()
  }
}

/**
 * Give the rendered cells a size, so the plugin has something to measure.
 *
 * jsdom lays nothing out and reports `offsetWidth` as 0 for every element,
 * which the plugin reads as "nothing to materialise". Every row is stubbed so
 * the assertions do not depend on which one is measured, and a merged cell is
 * given the width of the columns it covers put together, which is what a
 * browser would lay it out at. No fixture uses `rowspan`, so a row's cells are
 * exactly its columns in order.
 */
function stubColumnWidths(
  element: HTMLElement,
  tableIndex = 0,
  widths: readonly number[] = COLUMN_PIXEL_WIDTHS
): void {
  const table = element.querySelectorAll("table")[tableIndex]
  for (const row of Array.from(table.querySelectorAll("tr"))) {
    let column = 0
    for (const cell of Array.from(
      row.querySelectorAll<HTMLTableCellElement>("th, td")
    )) {
      const colspan = Math.max(cell.colSpan, 1)
      const width = widths
        .slice(column, column + colspan)
        .reduce((total, value) => total + value, 0)
      Object.defineProperty(cell, "offsetWidth", {
        configurable: true,
        get: () => width,
      })
      column += colspan
    }
  }
}

/** Every table in the document, as a grid of its cells' positions. */
function cellPositions(editor: Editor, tableIndex = 0): number[][] {
  return readTables(editor, (_cell, pos) => pos)[tableIndex]
}

/** Every table in the document, as a grid of its cells' `colwidth`. */
function columnWidths(editor: Editor, tableIndex = 0): (number[] | null)[][] {
  return readTables(editor, (cell) =>
    Array.isArray(cell.attrs.colwidth) ? [...cell.attrs.colwidth] : null
  )[tableIndex]
}

function readTables<T>(
  editor: Editor,
  readCell: (
    cell: ReturnType<Editor["state"]["doc"]["child"]>,
    pos: number
  ) => T
): T[][][] {
  const tables: T[][][] = []
  editor.state.doc.descendants((table, tablePos) => {
    if (table.type.name !== "table") {
      return true
    }
    const rows: T[][] = []
    table.forEach((row, rowOffset) => {
      const cells: T[] = []
      row.forEach((cell, cellOffset) => {
        // The table's content starts one past the table itself, and each row's
        // content one past the row.
        cells.push(readCell(cell, tablePos + 1 + rowOffset + 1 + cellOffset))
      })
      rows.push(cells)
    })
    tables.push(rows)
    return false
  })
  return tables
}

/** Inline widths the rendered `<col>` elements carry. */
function renderedColumnWidths(element: HTMLElement, tableIndex = 0): string[] {
  const table = element.querySelectorAll("table")[tableIndex]
  return Array.from(table.querySelectorAll("col")).map((col) => col.style.width)
}

/**
 * Arm the resize handle on the given cell's right edge and press it.
 *
 * Arming is dispatched rather than driven with a pointer because jsdom reports
 * a zero-sized rectangle for every element, so `columnResizing`'s `mousemove`
 * can never find a boundary to arm. The meta is the one its own `updateHandle`
 * sends.
 */
function pressHandle(editor: Editor, element: HTMLElement, cellPos: number) {
  editor.view.dispatch(
    editor.state.tr.setMeta(columnResizingPluginKey, { setHandle: cellPos })
  )

  const cell = element.querySelector("th")
  if (!cell) {
    throw new Error("no cell to press")
  }
  cell.dispatchEvent(
    new MouseEvent("mousedown", {
      bubbles: true,
      cancelable: true,
      clientX: PRESS_X,
      clientY: 10,
    })
  )

  // `columnResizing` starts the drag from its own mousedown handler, which runs
  // straight after this plugin's. Without a drag under way there is nothing to
  // settle and every assertion below would be vacuous.
  const resizeState = columnResizingPluginKey.getState(editor.state)
  if (!resizeState?.dragging) {
    throw new Error("columnResizing did not start a drag")
  }
}

/**
 * Move the pointer by `offsetX` and release it.
 *
 * Both events go to the window, which is where `columnResizing` listens once a
 * drag is under way, so this covers a pointer released outside the editor too.
 */
function releasePointer(offsetX: number): void {
  if (offsetX !== 0) {
    const move = new MouseEvent("mousemove", {
      clientX: PRESS_X + offsetX,
      clientY: 10,
    })
    // jsdom does not derive the legacy `which` from `buttons`, and upstream
    // reads it as "the button came up somewhere we did not see", ending the
    // drag on the first move. Shadowing it keeps the simulated drag in one
    // piece: move, then release.
    Object.defineProperty(move, "which", { value: 1 })
    window.dispatchEvent(move)
  }
  window.dispatchEvent(
    new MouseEvent("mouseup", { clientX: PRESS_X + offsetX, clientY: 10 })
  )
}

function serializeMarkdown(editor: Editor): string {
  const manager = new MarkdownManager({
    extensions,
    markedOptions: { gfm: true },
  })
  return manager.serialize(editor.getJSON() as JSONContent)
}

describe("materialising column widths on mousedown", () => {
  it("writes the on-screen widths onto every cell so the drag cannot snap", () => {
    withEditor(DERIVED_TABLE, (editor, element) => {
      stubColumnWidths(element)
      expect(renderedColumnWidths(element)).toEqual(["13.04%", "86.96%"])

      pressHandle(editor, element, cellPositions(editor)[0][0])

      // Every column, not only the dragged one, enters the drag at the width it
      // was already rendered at. `displayColumnWidth` rebuilds the `<col>`s
      // from these attributes as the drag starts; without them the columns the
      // user is not dragging would snap to an equal share.
      expect(columnWidths(editor)).toEqual([
        [[60], [240]],
        [[60], [240]],
      ])
      expect(renderedColumnWidths(element)).toEqual(["60px", "240px"])
    })
  })

  it("measures a merged column from a row that does not merge it", () => {
    withEditor(SPANNED_TABLE, (editor, element) => {
      stubColumnWidths(element, 0, SPANNED_PIXEL_WIDTHS)

      pressHandle(editor, element, cellPositions(editor)[0][0])

      // The header cell is 300px wide over two columns rendered at 60 and 240,
      // and halving it would fabricate 150/150 — snapping both columns on
      // mousedown and persisting the wrong proportions after the drag.
      expect(columnWidths(editor)).toEqual([
        [[60, 240], [100]],
        [[60], [240], [100]],
      ])
      expect(renderedColumnWidths(element)).toEqual(["60px", "240px", "100px"])
    })
  })

  it("splits a merged cell only when no row measures the column alone", () => {
    withEditor(FULLY_SPANNED_TABLE, (editor, element) => {
      stubColumnWidths(element, 0, SPANNED_PIXEL_WIDTHS)

      pressHandle(editor, element, cellPositions(editor)[0][0])

      // Both columns are merged in every row, so there is nothing better to
      // measure and an even split of the 300px cell is the last resort.
      expect(columnWidths(editor)).toEqual([
        [[150, 150], [100]],
        [[150, 150], [100]],
      ])
    })
  })

  it("leaves a table that already has explicit widths alone", () => {
    withEditor(SIZED_TABLE, (editor, element) => {
      stubColumnWidths(element)

      pressHandle(editor, element, cellPositions(editor)[0][0])
      const pressed = editor.state.doc.toJSON()
      expect(columnWidths(editor)).toEqual([
        [[100], [300]],
        [[100], [300]],
      ])

      releasePointer(0)

      // Nothing was materialised, so there is nothing to put back either.
      expect(editor.state.doc.toJSON()).toEqual(pressed)
    })
  })
})

describe("settling the materialisation when the drag ends", () => {
  it("puts every width back when the handle is clicked but never dragged", () => {
    withEditor(DERIVED_TABLE, (editor, element) => {
      stubColumnWidths(element)
      const markdownBefore = serializeMarkdown(editor)
      expect(markdownBefore).toMatch(/^\| Host\s+\|/m)

      pressHandle(editor, element, cellPositions(editor)[0][0])
      releasePointer(0)

      // A stray click near a column boundary is a no-op, not a permanent
      // conversion of the table.
      expect(columnWidths(editor)).toEqual([
        [null, null],
        [null, null],
      ])
      // Which is what keeps the table pure pipe Markdown: one explicit width
      // anywhere serialises the whole table as a raw HTML block. Trimmed
      // because the press leaves the document with the trailing empty
      // paragraph every editable document ends up with.
      expect(serializeMarkdown(editor).trim()).toBe(markdownBefore.trim())
      expect(serializeMarkdown(editor)).not.toContain("<table>")
      // And the derived proportions are back on screen.
      expect(renderedColumnWidths(element)).toEqual(["13.04%", "86.96%"])
    })
  })

  it("keeps the materialisation when the drag changed a column's width", () => {
    withEditor(DERIVED_TABLE, (editor, element) => {
      stubColumnWidths(element)

      pressHandle(editor, element, cellPositions(editor)[0][0])
      releasePointer(40)

      // The dragged column keeps the width the drag left it at, and the column
      // the user never touched keeps the width it was materialised with.
      expect(columnWidths(editor)).toEqual([
        [[100], [240]],
        [[100], [240]],
      ])
      const markdown = serializeMarkdown(editor)
      expect(markdown).toContain("<table>")
      expect(markdown).toContain('colwidth="100"')
      expect(markdown).toContain('colwidth="240"')
      expect(markdown).not.toContain("| Host |")
    })
  })

  it("reverts exactly the cells it wrote and nothing else", () => {
    withEditor(`${DERIVED_TABLE}${SIZED_TABLE}`, (editor, element) => {
      stubColumnWidths(element, 0)
      stubColumnWidths(element, 1)

      pressHandle(editor, element, cellPositions(editor, 0)[0][0])
      const pressed = editor.state.doc.toJSON()
      // Only the pressed table is materialised in the first place.
      expect(columnWidths(editor, 0)).toEqual([
        [[60], [240]],
        [[60], [240]],
      ])
      expect(columnWidths(editor, 1)).toEqual([
        [[100], [300]],
        [[100], [300]],
      ])

      releasePointer(0)

      expect(columnWidths(editor, 0)).toEqual([
        [null, null],
        [null, null],
      ])
      expect(columnWidths(editor, 1)).toEqual([
        [[100], [300]],
        [[100], [300]],
      ])
      // The revert is the only difference from the pressed document: the second
      // table, the text, and everything around them are untouched.
      expect(editor.state.doc.toJSON()).toEqual(
        withColumnWidths(pressed, 0, null)
      )
    })
  })

  it("leaves the materialisation in place when the cells have moved on", () => {
    withEditor(DERIVED_TABLE, (editor, element) => {
      stubColumnWidths(element)

      pressHandle(editor, element, cellPositions(editor)[0][0])

      // Something else — a collaborator, an agent — changing a width out from
      // under a pending materialisation. Reverting a document this plugin can
      // no longer account for would be worse than leaving the widths behind.
      // The whole column has to change together: `fixTables` runs after every
      // transaction and pulls a column whose cells disagree back into line.
      const tr = editor.state.tr
      for (const row of cellPositions(editor)) {
        const cellPos = row[1]
        const cell = editor.state.doc.nodeAt(cellPos)
        if (!cell) {
          throw new Error("no cell to change")
        }
        tr.setNodeMarkup(cellPos, null, { ...cell.attrs, colwidth: [180] })
      }
      editor.view.dispatch(tr)
      expect(columnWidths(editor)).toEqual([
        [[60], [180]],
        [[60], [180]],
      ])

      releasePointer(0)

      expect(columnWidths(editor)).toEqual([
        [[60], [180]],
        [[60], [180]],
      ])
    })
  })
})

/** The same document with one table's `colwidth` replaced everywhere. */
function withColumnWidths(
  doc: ReturnType<Editor["state"]["doc"]["toJSON"]>,
  tableIndex: number,
  colwidth: number[] | null
): JSONContent {
  const json = doc as JSONContent
  const tables = (json.content ?? []).filter((node) => node.type === "table")
  for (const row of tables[tableIndex]?.content ?? []) {
    for (const cell of row.content ?? []) {
      if (cell.attrs) {
        cell.attrs.colwidth = colwidth
      }
    }
  }
  return json
}
