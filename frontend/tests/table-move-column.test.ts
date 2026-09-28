import { TableCell } from "@tiptap/extension-table-cell"
import { TableHeader } from "@tiptap/extension-table-header"
import { TableRow } from "@tiptap/extension-table-row"
import { Editor } from "@tiptap/react"
import { StarterKit } from "@tiptap/starter-kit"
import { TracecatTable } from "@/components/tiptap-node/table-node/table-node-extension"

/** A cell as the assertions read it: content, persisted width, and node type. */
type CellSnapshot = {
  text: string
  colwidth: number[] | null
  type: string
}

/**
 * A three-column table whose columns carry distinct text and explicit widths,
 * so a move can be told apart from the widths staying with the position.
 */
const SIZED_TABLE = `
<table>
  <tbody>
    <tr>
      <th colwidth="100">H1</th>
      <th colwidth="200">H2</th>
      <th colwidth="300">H3</th>
    </tr>
    <tr>
      <td colwidth="100">a</td>
      <td colwidth="200">b</td>
      <td colwidth="300">c</td>
    </tr>
  </tbody>
</table>
`

/** The same shape, but with the first column of the body row a header cell. */
const ROW_HEADER_TABLE = `
<table>
  <tbody>
    <tr><th>H1</th><th>H2</th><th>H3</th></tr>
    <tr><th>a</th><td>b</td><td>c</td></tr>
  </tbody>
</table>
`

function createEditor(content: string): {
  editor: Editor
  element: HTMLElement
} {
  const element = document.createElement("div")
  document.body.appendChild(element)
  const editor = new Editor({
    element,
    extensions: [
      StarterKit,
      TracecatTable.configure({
        resizable: false,
      }),
      TableRow,
      TableHeader,
      TableCell,
    ],
    content,
  })
  return { editor, element }
}

function readColwidth(value: unknown): number[] | null {
  if (!Array.isArray(value)) {
    return null
  }
  return value.filter((entry): entry is number => typeof entry === "number")
}

/** Snapshot every cell of the document's table, row by row. */
function readTable(editor: Editor): CellSnapshot[][] {
  const rows: CellSnapshot[][] = []
  editor.state.doc.descendants((node) => {
    if (node.type.name !== "tableRow") {
      return true
    }
    const cells: CellSnapshot[] = []
    node.forEach((cell) => {
      cells.push({
        text: cell.textContent,
        colwidth: readColwidth(cell.attrs.colwidth),
        type: cell.type.name,
      })
    })
    rows.push(cells)
    // Rows never nest, so there is nothing below one worth descending into.
    return false
  })
  return rows
}

function cellTexts(editor: Editor): string[][] {
  return readTable(editor).map((row) => row.map((cell) => cell.text))
}

/** Document positions of every cell, indexed by row and then by column. */
function cellPositions(editor: Editor): number[][] {
  const rows: number[][] = []
  editor.state.doc.descendants((node, pos) => {
    if (node.type.name !== "tableRow") {
      return true
    }
    const positions: number[] = []
    node.forEach((_cell, offset) => {
      positions.push(pos + 1 + offset)
    })
    rows.push(positions)
    return false
  })
  return rows
}

/** Put a plain text cursor inside the given cell, as clicking into it would. */
function placeCursorInCell(editor: Editor, row: number, column: number): void {
  const cellPos = cellPositions(editor)[row][column]
  // +1 enters the cell, +1 again enters its first paragraph.
  editor.commands.setTextSelection(cellPos + 2)
}

function withEditor(content: string, run: (editor: Editor) => void): void {
  const { editor, element } = createEditor(content)
  try {
    run(editor)
  } finally {
    editor.destroy()
    element.remove()
  }
}

describe("moveTableColumnRight", () => {
  it("swaps the column's content with the column to its right", () => {
    withEditor(SIZED_TABLE, (editor) => {
      placeCursorInCell(editor, 1, 0)

      expect(editor.chain().focus().moveTableColumnRight().run()).toBe(true)

      expect(cellTexts(editor)).toEqual([
        ["H2", "H1", "H3"],
        ["b", "a", "c"],
      ])
    })
  })

  it("carries the moved column's colwidth with its content", () => {
    withEditor(SIZED_TABLE, (editor) => {
      placeCursorInCell(editor, 0, 0)

      expect(editor.commands.moveTableColumnRight()).toBe(true)

      // The widths follow "H1" and "a" into the second column rather than
      // staying with the position they were rendered at.
      expect(readTable(editor)).toEqual([
        [
          { text: "H2", colwidth: [200], type: "tableHeader" },
          { text: "H1", colwidth: [100], type: "tableHeader" },
          { text: "H3", colwidth: [300], type: "tableHeader" },
        ],
        [
          { text: "b", colwidth: [200], type: "tableCell" },
          { text: "a", colwidth: [100], type: "tableCell" },
          { text: "c", colwidth: [300], type: "tableCell" },
        ],
      ])
    })
  })

  it("keeps moving the same column on repeated presses", () => {
    withEditor(SIZED_TABLE, (editor) => {
      placeCursorInCell(editor, 1, 0)

      // The first move leaves a `CellSelection` over the moved column, which is
      // what lets the second press pick the same column up again.
      editor.chain().focus().moveTableColumnRight().run()
      editor.chain().focus().moveTableColumnRight().run()

      expect(cellTexts(editor)).toEqual([
        ["H2", "H3", "H1"],
        ["b", "c", "a"],
      ])
    })
  })
})

describe("moveTableColumnLeft", () => {
  it("swaps the column's content with the column to its left", () => {
    withEditor(SIZED_TABLE, (editor) => {
      placeCursorInCell(editor, 1, 2)

      expect(editor.chain().focus().moveTableColumnLeft().run()).toBe(true)

      expect(cellTexts(editor)).toEqual([
        ["H1", "H3", "H2"],
        ["a", "c", "b"],
      ])
    })
  })

  it("carries the moved column's colwidth with its content", () => {
    withEditor(SIZED_TABLE, (editor) => {
      placeCursorInCell(editor, 0, 2)

      expect(editor.commands.moveTableColumnLeft()).toBe(true)

      expect(readTable(editor)[0]).toEqual([
        { text: "H1", colwidth: [100], type: "tableHeader" },
        { text: "H3", colwidth: [300], type: "tableHeader" },
        { text: "H2", colwidth: [200], type: "tableHeader" },
      ])
    })
  })
})

describe("cell types across a move", () => {
  it("leaves the header row as header cells after a move into position 0", () => {
    withEditor(SIZED_TABLE, (editor) => {
      placeCursorInCell(editor, 0, 1)

      expect(editor.commands.moveTableColumnLeft()).toBe(true)

      const [headerRow, bodyRow] = readTable(editor)
      expect(headerRow.map((cell) => cell.text)).toEqual(["H2", "H1", "H3"])
      expect(headerRow.map((cell) => cell.type)).toEqual([
        "tableHeader",
        "tableHeader",
        "tableHeader",
      ])
      expect(bodyRow.map((cell) => cell.type)).toEqual([
        "tableCell",
        "tableCell",
        "tableCell",
      ])
    })
  })

  it("keeps a header column's cell type with the position, not the content", () => {
    withEditor(ROW_HEADER_TABLE, (editor) => {
      placeCursorInCell(editor, 1, 1)

      expect(editor.commands.moveTableColumnLeft()).toBe(true)

      // Content moves, node type does not: the body row's first cell is still a
      // header cell and the second is still a plain cell.
      expect(readTable(editor)[1]).toEqual([
        { text: "b", colwidth: null, type: "tableHeader" },
        { text: "a", colwidth: null, type: "tableCell" },
        { text: "c", colwidth: null, type: "tableCell" },
      ])
    })
  })
})

describe("availability", () => {
  it("cannot move the leftmost column left or the rightmost column right", () => {
    withEditor(SIZED_TABLE, (editor) => {
      placeCursorInCell(editor, 1, 0)
      expect(editor.can().moveTableColumnLeft()).toBe(false)
      expect(editor.can().moveTableColumnRight()).toBe(true)

      placeCursorInCell(editor, 1, 2)
      expect(editor.can().moveTableColumnRight()).toBe(false)
      expect(editor.can().moveTableColumnLeft()).toBe(true)
    })
  })

  it("leaves the document untouched when a boundary command runs anyway", () => {
    withEditor(SIZED_TABLE, (editor) => {
      placeCursorInCell(editor, 1, 0)
      const before = editor.state.doc.toJSON()

      expect(editor.commands.moveTableColumnLeft()).toBe(false)

      expect(editor.state.doc.toJSON()).toEqual(before)
    })
  })

  it("is unavailable when the selection is outside a table", () => {
    withEditor(`<p>outside</p>${SIZED_TABLE}`, (editor) => {
      editor.commands.setTextSelection(2)
      const before = editor.state.doc.toJSON()

      expect(editor.can().moveTableColumnLeft()).toBe(false)
      expect(editor.can().moveTableColumnRight()).toBe(false)
      expect(editor.commands.moveTableColumnLeft()).toBe(false)
      expect(editor.commands.moveTableColumnRight()).toBe(false)
      expect(editor.state.doc.toJSON()).toEqual(before)
    })
  })

  it("is unavailable in a document with no table at all", () => {
    withEditor("<p>plain paragraph</p>", (editor) => {
      editor.commands.setTextSelection(2)

      expect(editor.can().moveTableColumnLeft()).toBe(false)
      expect(editor.can().moveTableColumnRight()).toBe(false)
    })
  })
})
