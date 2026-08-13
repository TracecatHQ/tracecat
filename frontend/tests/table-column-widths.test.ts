import type { Node as ProseMirrorNode } from "@tiptap/pm/model"
import { Schema } from "@tiptap/pm/model"
import { tableNodes } from "@tiptap/pm/tables"
import {
  applyColumnPercentages,
  applyDerivedColumnWidths,
  columnPercentagesFromWeights,
  columnWeightsAreReordered,
  deriveColumnWeights,
  deriveColumnWidths,
  MAX_COLUMN_WEIGHT_CHARS,
  MIN_COLUMN_WEIGHT_CHARS,
  tableNodeHasExplicitWidths,
} from "@/components/tiptap-node/table-node/table-column-widths"
import { TracecatTableView } from "@/components/tiptap-node/table-node/table-node-extension"

const schema = new Schema({
  nodes: {
    doc: { content: "block+" },
    paragraph: {
      content: "inline*",
      group: "block",
      toDOM: () => ["p", 0],
    },
    text: { group: "inline" },
    ...tableNodes({
      tableGroup: "block",
      cellContent: "paragraph+",
      cellAttributes: {},
    }),
  },
})

type CellAttrs = { colspan?: number; colwidth?: (number | null)[] | null }

function makeCell(
  text: string,
  attrs: CellAttrs = {},
  isHeader = false
): ProseMirrorNode {
  const paragraph =
    text.length > 0
      ? schema.nodes.paragraph.create(null, schema.text(text))
      : schema.nodes.paragraph.create()
  const type = isHeader ? schema.nodes.table_header : schema.nodes.table_cell
  return type.create(attrs, paragraph)
}

/**
 * Build a table from a grid of cell texts. The first row is a header row.
 * `attrs` is applied to the cell at the matching grid coordinates.
 */
function makeTable(
  rows: string[][],
  attrsByCoord: Record<string, CellAttrs> = {}
): ProseMirrorNode {
  const rowNodes = rows.map((cells, rowIndex) =>
    schema.nodes.table_row.create(
      null,
      cells.map((text, cellIndex) =>
        makeCell(
          text,
          attrsByCoord[`${rowIndex}:${cellIndex}`] ?? {},
          rowIndex === 0
        )
      )
    )
  )
  return schema.nodes.table.create(null, rowNodes)
}

function sum(values: number[]): number {
  return values.reduce((total, value) => total + value, 0)
}

/** The percentage half of a derivation, or `null` when nothing was derived. */
function deriveColumnPercentages(table: ProseMirrorNode): number[] | null {
  return deriveColumnWidths(table)?.percentages ?? null
}

describe("columnPercentagesFromWeights", () => {
  it("splits equal weights evenly", () => {
    expect(columnPercentagesFromWeights([10, 10, 10, 10])).toEqual([
      25, 25, 25, 25,
    ])
  })

  it("clamps weights below the floor", () => {
    // 1 and 5 characters both clamp to MIN_COLUMN_WEIGHT_CHARS.
    expect(columnPercentagesFromWeights([1, 5])).toEqual([50, 50])
    expect(columnPercentagesFromWeights([0, MIN_COLUMN_WEIGHT_CHARS])).toEqual([
      50, 50,
    ])
  })

  it("clamps weights above the ceiling", () => {
    // 64 and 512 characters both clamp to MAX_COLUMN_WEIGHT_CHARS.
    expect(columnPercentagesFromWeights([64, 512])).toEqual([50, 50])
    // 40 vs 6 is the widest spread the clamps allow.
    expect(
      columnPercentagesFromWeights([512, MIN_COLUMN_WEIGHT_CHARS])
    ).toEqual([86.96, 13.04])
  })

  it("adjusts the last entry so percentages sum to exactly 100", () => {
    const percentages = columnPercentagesFromWeights([7, 11, 23])
    expect(percentages).not.toBeNull()
    expect(sum(percentages as number[])).toBe(100)
  })

  it("keeps three columns within a hundredth of a percent of each other", () => {
    const percentages = columnPercentagesFromWeights([10, 10, 10])
    expect(percentages).toEqual([33.33, 33.33, 33.34])
    expect(sum(percentages as number[])).toBe(100)
  })

  it("ignores non-finite weights", () => {
    expect(columnPercentagesFromWeights([Number.NaN, 10])).toEqual([37.5, 62.5])
  })

  it("returns null when there are no columns", () => {
    expect(columnPercentagesFromWeights([])).toBeNull()
  })
})

describe("deriveColumnWidths", () => {
  it("gives a freshly inserted empty table equal columns", () => {
    expect(
      deriveColumnPercentages(
        makeTable([
          ["", "", "", ""],
          ["", "", "", ""],
        ])
      )
    ).toEqual([25, 25, 25, 25])

    expect(
      deriveColumnPercentages(
        makeTable([
          ["", ""],
          ["", ""],
          ["", ""],
        ])
      )
    ).toEqual([50, 50])
  })

  it("gives a fresh 3x2 empty table columns that sum to 100", () => {
    const percentages = deriveColumnPercentages(
      makeTable([
        ["", "", ""],
        ["", "", ""],
      ])
    )
    expect(percentages).toEqual([33.33, 33.33, 33.34])
    expect(sum(percentages as number[])).toBe(100)
  })

  it("weights columns by their longest cell, header row included", () => {
    // Column weights clamp to [6, 40]: 40 and 6.
    expect(
      deriveColumnPercentages(
        makeTable([
          ["a".repeat(80), "b"],
          ["c", "d"],
        ])
      )
    ).toEqual([86.96, 13.04])

    // The header alone can be the longest text in its column.
    expect(
      deriveColumnPercentages(
        makeTable([
          ["a".repeat(80), "b"],
          ["", ""],
        ])
      )
    ).toEqual([86.96, 13.04])
  })

  it("divides a spanning cell's text across the columns it covers", () => {
    // A colspan-2 header holding 80 characters counts as 40 per column, which
    // is the ceiling, so both columns come out equal.
    expect(
      deriveColumnPercentages(
        makeTable([["a".repeat(80)], ["", ""]], { "0:0": { colspan: 2 } })
      )
    ).toEqual([50, 50])

    // 24 characters over two columns is 12 per column; the second column's own
    // 30-character cell still wins there.
    expect(
      deriveColumnPercentages(
        makeTable([["a".repeat(24)], ["", "b".repeat(30)]], {
          "0:0": { colspan: 2 },
        })
      )
    ).toEqual([28.57, 71.43])
  })

  it("returns null for a node that is not a table", () => {
    const paragraph = schema.nodes.paragraph.create(null, schema.text("hello"))
    expect(deriveColumnWidths(paragraph)).toBeNull()
  })

  it("returns the clamped weights the percentages were built from", () => {
    expect(
      deriveColumnWidths(
        makeTable([
          ["a".repeat(80), "b"],
          ["c", "d"],
        ])
      )
    ).toEqual({
      weights: [MAX_COLUMN_WEIGHT_CHARS, MIN_COLUMN_WEIGHT_CHARS],
      percentages: [86.96, 13.04],
    })
  })
})

describe("deriveColumnWeights", () => {
  it("clamps the longest cell text of each column", () => {
    expect(
      deriveColumnWeights(
        makeTable([
          ["a".repeat(80), "b", "c".repeat(12)],
          ["d", "e", "f"],
        ])
      )
    ).toEqual([MAX_COLUMN_WEIGHT_CHARS, MIN_COLUMN_WEIGHT_CHARS, 12])
  })

  it("returns one weight per column a spanning cell covers", () => {
    expect(
      deriveColumnWeights(
        makeTable([["a"], ["b", "c"]], { "0:0": { colspan: 2 } })
      )
    ).toEqual([MIN_COLUMN_WEIGHT_CHARS, MIN_COLUMN_WEIGHT_CHARS])
  })

  it("returns null for a node that is not a table", () => {
    const paragraph = schema.nodes.paragraph.create(null, schema.text("hello"))
    expect(deriveColumnWeights(paragraph)).toBeNull()
  })
})

describe("columnWeightsAreReordered", () => {
  it("reports a swap of two columns", () => {
    expect(columnWeightsAreReordered([6, 40], [40, 6])).toBe(true)
  })

  it("reports a rotation of three columns", () => {
    expect(columnWeightsAreReordered([10, 30, 20], [10, 20, 30])).toBe(true)
  })

  it("handles repeated weights as a multiset", () => {
    expect(columnWeightsAreReordered([10, 20, 10], [10, 10, 20])).toBe(true)
    // Same set of distinct values, different counts: not a reordering.
    expect(columnWeightsAreReordered([10, 20, 20], [10, 10, 20])).toBe(false)
  })

  it("does not report identical weights", () => {
    expect(columnWeightsAreReordered([10, 20, 30], [10, 20, 30])).toBe(false)
    expect(columnWeightsAreReordered([], [])).toBe(false)
  })

  it("does not report a single changed weight", () => {
    // Typing into one column can only ever move one weight, which changes the
    // multiset unless the edit was a no-op.
    expect(columnWeightsAreReordered([20, 20, 30], [10, 20, 30])).toBe(false)
    expect(columnWeightsAreReordered([40, 40], [40, 6])).toBe(false)
  })

  it("does not report lists of different lengths", () => {
    expect(columnWeightsAreReordered([10, 20], [10, 20, 30])).toBe(false)
  })
})

describe("tableNodeHasExplicitWidths", () => {
  it("treats a positive colwidth entry as explicit", () => {
    expect(
      tableNodeHasExplicitWidths(
        makeTable(
          [
            ["a", "b"],
            ["c", "d"],
          ],
          { "0:0": { colwidth: [0, 300] } }
        )
      )
    ).toBe(true)
  })

  it("treats a zero-seeded colwidth as not explicit", () => {
    expect(
      tableNodeHasExplicitWidths(
        makeTable(
          [
            ["a", "b"],
            ["c", "d"],
          ],
          { "0:0": { colwidth: [0] } }
        )
      )
    ).toBe(false)
  })

  it("treats a null-filled colwidth as not explicit", () => {
    expect(
      tableNodeHasExplicitWidths(
        makeTable(
          [
            ["a", "b"],
            ["c", "d"],
          ],
          { "0:0": { colwidth: [null] } }
        )
      )
    ).toBe(false)
  })

  it("treats a missing colwidth as not explicit", () => {
    expect(
      tableNodeHasExplicitWidths(
        makeTable([
          ["a", "b"],
          ["c", "d"],
        ])
      )
    ).toBe(false)
  })
})

function makeRenderedTable(columnCount: number): {
  colgroup: HTMLTableColElement
  tableEl: HTMLTableElement
} {
  const tableEl = document.createElement("table")
  const colgroup = tableEl.appendChild(document.createElement("colgroup"))
  for (let index = 0; index < columnCount; index += 1) {
    const col = colgroup.appendChild(document.createElement("col"))
    // Mirror what upstream leaves behind on a width-less column.
    col.style.setProperty("min-width", "25px")
  }
  tableEl.style.minWidth = `${columnCount * 25}px`
  return { colgroup, tableEl }
}

describe("applyDerivedColumnWidths", () => {
  it("writes percentage widths and clears the stale min-width", () => {
    const { colgroup, tableEl } = makeRenderedTable(2)
    const applied = applyDerivedColumnWidths(
      makeTable([
        ["a".repeat(80), "b"],
        ["c", "d"],
      ]),
      colgroup,
      tableEl
    )

    expect(applied).toEqual({
      weights: [MAX_COLUMN_WEIGHT_CHARS, MIN_COLUMN_WEIGHT_CHARS],
      percentages: [86.96, 13.04],
    })
    const cols = Array.from(colgroup.querySelectorAll("col"))
    expect(cols.map((col) => col.style.width)).toEqual(["86.96%", "13.04%"])
    expect(cols.map((col) => col.style.minWidth)).toEqual(["", ""])
    expect(tableEl.style.width).toBe("")
    expect(tableEl.style.minWidth).toBe("")
  })

  it("leaves explicitly sized tables entirely to upstream", () => {
    const { colgroup, tableEl } = makeRenderedTable(2)
    const applied = applyDerivedColumnWidths(
      makeTable(
        [
          ["a", "b"],
          ["c", "d"],
        ],
        { "0:0": { colwidth: [0, 300] } }
      ),
      colgroup,
      tableEl
    )

    expect(applied).toBeNull()
    const cols = Array.from(colgroup.querySelectorAll("col"))
    expect(cols.map((col) => col.style.width)).toEqual(["", ""])
    expect(cols.map((col) => col.style.minWidth)).toEqual(["25px", "25px"])
    expect(tableEl.style.minWidth).toBe("50px")
  })

  it("clears previously derived widths when the column count disagrees", () => {
    const { colgroup, tableEl } = makeRenderedTable(3)
    for (const col of Array.from(colgroup.querySelectorAll("col"))) {
      col.style.width = "33.33%"
    }

    const applied = applyDerivedColumnWidths(
      makeTable([
        ["a", "b"],
        ["c", "d"],
      ]),
      colgroup,
      tableEl
    )

    expect(applied).toBeNull()
    expect(
      Array.from(colgroup.querySelectorAll("col")).map((col) => col.style.width)
    ).toEqual(["", "", ""])
  })

  it("clears previously derived widths for a malformed table", () => {
    const { colgroup, tableEl } = makeRenderedTable(2)
    for (const col of Array.from(colgroup.querySelectorAll("col"))) {
      col.style.width = "50%"
    }

    const paragraph = schema.nodes.paragraph.create(null, schema.text("hello"))
    const applied = applyDerivedColumnWidths(paragraph, colgroup, tableEl)

    expect(applied).toBeNull()
    expect(
      Array.from(colgroup.querySelectorAll("col")).map((col) => col.style.width)
    ).toEqual(["", ""])
  })
})

describe("applyColumnPercentages", () => {
  it("writes the given percentages without measuring the table", () => {
    const { colgroup, tableEl } = makeRenderedTable(2)
    // Content that would derive to [86.96, 13.04] if it were measured.
    const applied = applyColumnPercentages(
      makeTable([
        ["a".repeat(80), "b"],
        ["c", "d"],
      ]),
      colgroup,
      tableEl,
      [20, 80]
    )

    expect(applied).toBe(true)
    const cols = Array.from(colgroup.querySelectorAll("col"))
    expect(cols.map((col) => col.style.width)).toEqual(["20%", "80%"])
    expect(cols.map((col) => col.style.minWidth)).toEqual(["", ""])
    expect(tableEl.style.minWidth).toBe("")
  })

  it("leaves explicitly sized tables entirely to upstream", () => {
    const { colgroup, tableEl } = makeRenderedTable(2)
    const applied = applyColumnPercentages(
      makeTable(
        [
          ["a", "b"],
          ["c", "d"],
        ],
        { "0:0": { colwidth: [0, 300] } }
      ),
      colgroup,
      tableEl,
      [20, 80]
    )

    expect(applied).toBe(false)
    const cols = Array.from(colgroup.querySelectorAll("col"))
    expect(cols.map((col) => col.style.width)).toEqual(["", ""])
    expect(cols.map((col) => col.style.minWidth)).toEqual(["25px", "25px"])
    expect(tableEl.style.minWidth).toBe("50px")
  })

  it("clears the widths when the percentages no longer fit the columns", () => {
    const { colgroup, tableEl } = makeRenderedTable(3)
    for (const col of Array.from(colgroup.querySelectorAll("col"))) {
      col.style.width = "33.33%"
    }

    const applied = applyColumnPercentages(
      makeTable([
        ["a", "b", "c"],
        ["d", "e", "f"],
      ]),
      colgroup,
      tableEl,
      [20, 80]
    )

    expect(applied).toBe(false)
    expect(
      Array.from(colgroup.querySelectorAll("col")).map((col) => col.style.width)
    ).toEqual(["", "", ""])
  })
})

describe("TracecatTableView", () => {
  const CELL_MIN_WIDTH = 25

  function colWidths(view: TracecatTableView): string[] {
    return Array.from(view.colgroup.querySelectorAll("col")).map(
      (col) => col.style.width
    )
  }

  function colMinWidths(view: TracecatTableView): string[] {
    return Array.from(view.colgroup.querySelectorAll("col")).map(
      (col) => col.style.minWidth
    )
  }

  it("derives proportional widths on mount", () => {
    const view = new TracecatTableView(
      makeTable([
        ["a".repeat(80), "b"],
        ["c", "d"],
      ]),
      CELL_MIN_WIDTH
    )

    expect(view.dom.className).toBe("tableWrapper")
    expect(colWidths(view)).toEqual(["86.96%", "13.04%"])
    expect(colMinWidths(view)).toEqual(["", ""])
    expect(view.table.style.minWidth).toBe("")
  })

  it("keeps the derived widths when only the cell text changes", () => {
    const view = new TracecatTableView(
      makeTable([
        ["a".repeat(80), "b"],
        ["c", "d"],
      ]),
      CELL_MIN_WIDTH
    )

    // Typing into the short column would derive [50, 50] if it recomputed.
    const handled = view.update(
      makeTable([
        ["a".repeat(80), "b".repeat(80)],
        ["c", "d"],
      ])
    )

    expect(handled).toBe(true)
    expect(colWidths(view)).toEqual(["86.96%", "13.04%"])
    // `super.update()` writes the stale min-width back, so the cached
    // percentages have to be re-applied rather than merely left in place.
    expect(colMinWidths(view)).toEqual(["", ""])
    expect(view.table.style.minWidth).toBe("")
  })

  it("keeps the derived widths across repeated content changes", () => {
    const view = new TracecatTableView(
      makeTable([
        ["a".repeat(80), "b"],
        ["c", "d"],
      ]),
      CELL_MIN_WIDTH
    )

    for (let length = 1; length <= 25; length += 1) {
      view.update(
        makeTable([
          ["a".repeat(80), "b".repeat(length)],
          ["c", "d"],
        ])
      )
    }

    expect(colWidths(view)).toEqual(["86.96%", "13.04%"])
  })

  it("re-derives when a column is moved", () => {
    const view = new TracecatTableView(
      makeTable([
        ["a".repeat(80), "b"],
        ["c", "d"],
      ]),
      CELL_MIN_WIDTH
    )
    expect(colWidths(view)).toEqual(["86.96%", "13.04%"])

    // The same two columns, swapped: the wide one has to stay wide.
    const handled = view.update(
      makeTable([
        ["b", "a".repeat(80)],
        ["d", "c"],
      ])
    )

    expect(handled).toBe(true)
    expect(colWidths(view)).toEqual(["13.04%", "86.96%"])
    expect(colMinWidths(view)).toEqual(["", ""])
    expect(view.table.style.minWidth).toBe("")

    // The moved order is the new baseline: typing still does not reflow.
    view.update(
      makeTable([
        ["b".repeat(80), "a".repeat(80)],
        ["d", "c"],
      ])
    )
    expect(colWidths(view)).toEqual(["13.04%", "86.96%"])
  })

  it("re-derives when a column moves past two others", () => {
    const view = new TracecatTableView(
      makeTable([
        ["a".repeat(10), "b".repeat(20), "c".repeat(30)],
        ["d", "e", "f"],
      ]),
      CELL_MIN_WIDTH
    )
    expect(colWidths(view)).toEqual(["16.67%", "33.33%", "50%"])

    view.update(
      makeTable([
        ["a".repeat(10), "c".repeat(30), "b".repeat(20)],
        ["d", "f", "e"],
      ])
    )

    expect(colWidths(view)).toEqual(["16.67%", "50%", "33.33%"])
  })

  it("keeps the derived widths when an edit changes a single weight", () => {
    const view = new TracecatTableView(
      makeTable([
        ["a".repeat(10), "b".repeat(20), "c".repeat(30)],
        ["d", "e", "f"],
      ]),
      CELL_MIN_WIDTH
    )
    expect(colWidths(view)).toEqual(["16.67%", "33.33%", "50%"])

    // Typing in the first column until it weighs the same as the second. The
    // multiset of weights changes, so this is a content edit, not a move.
    view.update(
      makeTable([
        ["a".repeat(20), "b".repeat(20), "c".repeat(30)],
        ["d", "e", "f"],
      ])
    )

    expect(colWidths(view)).toEqual(["16.67%", "33.33%", "50%"])
    expect(colMinWidths(view)).toEqual(["", "", ""])
  })

  it("re-derives when a column is inserted", () => {
    const view = new TracecatTableView(
      makeTable([
        ["a".repeat(80), "b"],
        ["c", "d"],
      ]),
      CELL_MIN_WIDTH
    )

    view.update(
      makeTable([
        ["a".repeat(80), "b", "c"],
        ["d", "e", "f"],
      ])
    )

    expect(colWidths(view)).toEqual(["76.92%", "11.54%", "11.54%"])
    expect(colMinWidths(view)).toEqual(["", "", ""])

    // The new column count becomes the baseline for later content changes.
    view.update(
      makeTable([
        ["a".repeat(80), "b".repeat(80), "c"],
        ["d", "e", "f"],
      ])
    )
    expect(colWidths(view)).toEqual(["76.92%", "11.54%", "11.54%"])
  })

  it("re-derives when a column is deleted", () => {
    const view = new TracecatTableView(
      makeTable([
        ["a".repeat(80), "b", "c"],
        ["d", "e", "f"],
      ]),
      CELL_MIN_WIDTH
    )
    expect(colWidths(view)).toEqual(["76.92%", "11.54%", "11.54%"])

    view.update(
      makeTable([
        ["a".repeat(80), "b"],
        ["d", "e"],
      ])
    )

    expect(colWidths(view)).toEqual(["86.96%", "13.04%"])
  })

  it("never touches an explicitly sized table", () => {
    const view = new TracecatTableView(
      makeTable(
        [
          ["a".repeat(80), "b"],
          ["c", "d"],
        ],
        { "0:0": { colwidth: [0, 300] } }
      ),
      CELL_MIN_WIDTH
    )

    expect(colWidths(view)).toEqual(["", ""])
    expect(colMinWidths(view)).toEqual(["25px", "25px"])

    view.update(
      makeTable(
        [
          ["a".repeat(80), "b".repeat(80)],
          ["c", "d"],
        ],
        { "0:0": { colwidth: [0, 300] } }
      )
    )

    expect(colWidths(view)).toEqual(["", ""])
    expect(colMinWidths(view)).toEqual(["25px", "25px"])
  })

  it("never touches an explicitly sized table when its columns move", () => {
    const view = new TracecatTableView(
      makeTable(
        [
          ["a".repeat(80), "b"],
          ["c", "d"],
        ],
        { "0:0": { colwidth: [0, 300] } }
      ),
      CELL_MIN_WIDTH
    )
    expect(colWidths(view)).toEqual(["", ""])

    // The explicit width travels with the column it belongs to, as
    // `moveTableColumn` moves it.
    view.update(
      makeTable(
        [
          ["b", "a".repeat(80)],
          ["d", "c"],
        ],
        { "0:1": { colwidth: [0, 300] } }
      )
    )

    expect(colWidths(view)).toEqual(["", ""])
    expect(colMinWidths(view)).toEqual(["25px", "25px"])
  })

  it("derives on the first update that yields a usable table", () => {
    // A table whose only row has no cells has no columns to measure.
    const view = new TracecatTableView(
      schema.nodes.table.create(null, schema.nodes.table_row.create(null, [])),
      CELL_MIN_WIDTH
    )
    expect(colWidths(view)).toEqual([])

    view.update(
      makeTable([
        ["a".repeat(80), "b"],
        ["c", "d"],
      ])
    )

    expect(colWidths(view)).toEqual(["86.96%", "13.04%"])
  })
})

describe("column weight clamps", () => {
  it("caps the spread between the widest and narrowest column", () => {
    expect(MIN_COLUMN_WEIGHT_CHARS).toBe(6)
    expect(MAX_COLUMN_WEIGHT_CHARS).toBe(40)
    expect(MAX_COLUMN_WEIGHT_CHARS / MIN_COLUMN_WEIGHT_CHARS).toBeCloseTo(
      6.67,
      2
    )
  })
})
