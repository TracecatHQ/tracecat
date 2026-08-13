import type { Node as ProseMirrorNode } from "@tiptap/pm/model"
import { Plugin, PluginKey } from "@tiptap/pm/state"
import { columnResizingPluginKey, TableMap } from "@tiptap/pm/tables"
import type { EditorView } from "@tiptap/pm/view"
import { tableNodeHasExplicitWidths } from "@/components/tiptap-node/table-node/table-column-widths"

/** Identifies the materialisation plugin, so its ordering can be asserted. */
export const materializeColumnWidthsPluginKey = new PluginKey(
  "tracecatMaterializeColumnWidths"
)

/**
 * Promote a table's on-screen column widths into `colwidth` when a resize
 * handle is grabbed.
 *
 * Grabbing a resize handle is an explicit statement of intent about widths: it
 * promotes the whole table from derived and presentational to explicit and
 * persisted, all at once. Before the drag the columns are proportioned by
 * `table-column-widths.ts` from the cell text, with every `colwidth` still
 * null; after it they are the user's, and they are written to Markdown as HTML
 * so they survive a reload (see `table-markdown.ts`).
 *
 * This has to happen on mousedown rather than on mouseup. `columnResizing`
 * calls `displayColumnWidth` as soon as the drag starts, which rebuilds every
 * `<col>` from the node's attributes; on a derived-width table those are all
 * null, so every column the user is not dragging snaps to equal width for the
 * duration of the drag and the derived proportions are gone by the time the
 * mouse comes up.
 *
 * The plugin must be installed ahead of `columnResizing`; see
 * `TracecatTable.addProseMirrorPlugins`.
 */
export function createMaterializeColumnWidthsPlugin(): Plugin {
  return new Plugin({
    key: materializeColumnWidthsPluginKey,
    props: {
      handleDOMEvents: {
        mousedown: (view, event) => {
          materializeColumnWidths(view, event)
          // Never handle the event: `columnResizing` runs straight afterwards
          // and starts the drag from the widths just written. An attribute-only
          // `setNodeMarkup` does not move positions, so its `activeHandle` is
          // still valid.
          return false
        },
      },
    },
  })
}

function materializeColumnWidths(view: EditorView, event: MouseEvent): void {
  // Mirror `columnResizing`'s own mousedown guard, so widths are materialised
  // exactly when a resize is about to begin and never otherwise.
  if (!view.editable || event.button !== 0) {
    return
  }
  const resizeState = columnResizingPluginKey.getState(view.state)
  if (!resizeState || resizeState.activeHandle === -1 || resizeState.dragging) {
    return
  }

  const $cell = view.state.doc.resolve(resizeState.activeHandle)
  const table = $cell.node(-1)
  if (!table) {
    return
  }
  // The same hand-off rule the derived-width layer uses: once any explicit
  // width exists the table belongs to upstream, and re-measuring would only
  // churn widths the user already chose.
  if (tableNodeHasExplicitWidths(table)) {
    return
  }

  const tableStart = $cell.start(-1)
  const map = readTableMap(table)
  if (!map) {
    return
  }

  const columnWidths = measureColumnWidths(view, table, map, tableStart)
  if (!columnWidths) {
    return
  }

  const tr = view.state.tr
  for (let row = 0; row < map.height; row += 1) {
    for (let col = 0; col < map.width; col += 1) {
      const index = row * map.width + col
      const cellPos = map.map[index]
      // Visit each cell once, at its top-left corner.
      if (col > 0 && map.map[index - 1] === cellPos) {
        continue
      }
      if (row > 0 && map.map[index - map.width] === cellPos) {
        continue
      }
      const cell = table.nodeAt(cellPos)
      if (!cell) {
        continue
      }
      const colspan = readColspan(cell.attrs.colspan)
      const colwidth = columnWidths.slice(
        col,
        Math.min(col + colspan, map.width)
      )
      if (colwidth.length === 0) {
        continue
      }
      tr.setNodeMarkup(tableStart + cellPos, null, { ...cell.attrs, colwidth })
    }
  }

  if (!tr.docChanged) {
    return
  }
  // Keep the resize a single undo step. The materialised widths are visually
  // identical to what the user was already looking at, so an undo entry for
  // them would read as a no-op press that has to be repeated.
  view.dispatch(tr.setMeta("addToHistory", false))
}

/**
 * Measure what the user can currently see: the rendered width of each column,
 * read off the first row's cells.
 */
function measureColumnWidths(
  view: EditorView,
  table: ProseMirrorNode,
  map: TableMap,
  tableStart: number
): number[] | null {
  const widths = new Array<number>(map.width).fill(0)

  let col = 0
  while (col < map.width) {
    const cellPos = map.map[col]
    const cell = table.nodeAt(cellPos)
    if (!cell) {
      return null
    }
    const dom = view.nodeDOM(tableStart + cellPos)
    if (!(dom instanceof HTMLElement)) {
      return null
    }
    const colspan = Math.min(readColspan(cell.attrs.colspan), map.width - col)
    const share = Math.round(dom.offsetWidth / colspan)
    if (share <= 0) {
      return null
    }
    for (let offset = 0; offset < colspan; offset += 1) {
      widths[col + offset] = share
    }
    col += colspan
  }

  return widths
}

function readTableMap(table: ProseMirrorNode): TableMap | null {
  try {
    // Throws for tables that are still malformed, e.g. before `fixTables` runs.
    return TableMap.get(table)
  } catch {
    return null
  }
}

function readColspan(value: unknown): number {
  if (typeof value === "number" && Number.isFinite(value) && value > 0) {
    return Math.round(value)
  }
  return 1
}
