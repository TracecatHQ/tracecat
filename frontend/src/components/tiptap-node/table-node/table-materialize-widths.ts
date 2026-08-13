import type { Attrs, Node as ProseMirrorNode } from "@tiptap/pm/model"
import type { EditorState, Transaction } from "@tiptap/pm/state"
import { Plugin, PluginKey } from "@tiptap/pm/state"
import { columnResizingPluginKey, type TableMap } from "@tiptap/pm/tables"
import type { EditorView } from "@tiptap/pm/view"
import {
  readSpan,
  readTableMap,
  tableNodeHasExplicitWidths,
} from "@/components/tiptap-node/table-node/table-column-widths"

/**
 * One cell whose `colwidth` was written on mousedown: where it was written, the
 * value written, and the value to put back if the drag never happens.
 */
type ProvisionalCellWidth = {
  /** Position of the cell in the document the write produced. */
  pos: number
  /** The exact `colwidth` written to that cell. */
  colwidth: readonly number[]
  /** The `colwidth` the cell carried before the write. */
  restore: unknown
}

/**
 * The cells the most recent mousedown materialised, still awaiting the verdict
 * of the drag, or `null` when nothing is pending.
 */
type ProvisionalWidths = readonly ProvisionalCellWidth[] | null

/** Sets or clears the pending record from a transaction. */
type ProvisionalWidthsMeta = { pending: ProvisionalWidths }

/** Identifies the materialisation plugin, so its ordering can be asserted. */
export const materializeColumnWidthsPluginKey =
  new PluginKey<ProvisionalWidths>("tracecatMaterializeColumnWidths")

/**
 * Promote a table's on-screen column widths into `colwidth` when a resize
 * handle is dragged.
 *
 * Dragging a resize handle is an explicit statement of intent about widths: it
 * promotes the whole table from derived and presentational to explicit and
 * persisted, all at once. Before the drag the columns are proportioned by
 * `table-column-widths.ts` from the cell text, with every `colwidth` still
 * null; after it they are the user's, and they are written to Markdown as HTML
 * so they survive a reload (see `table-markdown.ts`).
 *
 * Merely clicking near a column boundary must not promote anything. That is a
 * real risk rather than a theoretical one: `columnResizing` arms its handle
 * whenever the pointer is within `handleWidth` of a boundary, a band that
 * overlaps the padding a user clicks to place the caret at the edge of a cell.
 *
 * The two pull in opposite directions, because the widths have to be written on
 * mousedown. `columnResizing` calls `displayColumnWidth` as soon as the drag
 * starts, which rebuilds every `<col>` from the node's attributes; on a
 * derived-width table those are all null, so every column the user is not
 * dragging would snap to equal width for the duration of the drag and the
 * derived proportions would be gone by the time the mouse came up.
 *
 * So the mousedown write is treated as provisional. The cells it touches are
 * recorded in this plugin's state, and when the drag ends the record is
 * settled: if the widths are exactly the ones that were written, no resize
 * happened and a compensating transaction puts the cells back, leaving the
 * table pure pipe Markdown. If they differ, the user resized something and the
 * materialisation stands.
 *
 * The plugin must be installed ahead of `columnResizing`; see
 * `TracecatTable.addProseMirrorPlugins`.
 */
export function createMaterializeColumnWidthsPlugin(): Plugin<ProvisionalWidths> {
  return new Plugin<ProvisionalWidths>({
    key: materializeColumnWidthsPluginKey,
    state: {
      init: () => null,
      apply: (tr, pending) => mapProvisionalWidths(tr, pending),
    },
    // Reverting from here rather than from a `mouseup` handler is what makes
    // the compensating write correct and cheap. `columnResizing` ends a drag
    // through window-level listeners of its own, so it always applies the final
    // width — via `updateColumnWidth` — before the transaction that clears
    // `dragging`, which is the transaction this responds to. A `mouseup`
    // handler on the editor DOM would run before both, and its revert would
    // then be undone by `updateColumnWidth` writing the dragged column's width
    // back onto a table that no longer had any. A window-level listener would
    // have to be ordered against upstream's own and removed on every exit path;
    // this needs no listener at all, and so covers a mouse released outside the
    // editor too.
    appendTransaction: (_transactions, oldState, newState) =>
      settleProvisionalWidths(oldState, newState),
    props: {
      handleDOMEvents: {
        mousedown: (view) => {
          materializeColumnWidths(view)
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

/**
 * Carry the pending record across a transaction.
 *
 * Positions are mapped rather than trusted: an attribute-only `setNodeMarkup`
 * moves nothing, but a collaborator's edit between mousedown and mouseup can
 * move everything. Mapped positions are still only a starting point — they are
 * re-resolved and checked before anything is written back.
 */
function mapProvisionalWidths(
  tr: Transaction,
  pending: ProvisionalWidths
): ProvisionalWidths {
  const meta = tr.getMeta(materializeColumnWidthsPluginKey) as
    | ProvisionalWidthsMeta
    | undefined
  if (meta) {
    return meta.pending
  }
  if (!pending || !tr.docChanged) {
    return pending
  }
  return pending.map((cell) => ({
    ...cell,
    pos: tr.mapping.map(cell.pos, -1),
  }))
}

/**
 * Settle a pending materialisation once the drag it was written for has ended.
 *
 * Returns `null` while no drag is ending or nothing is pending, an empty
 * transaction that only drops the record when the materialisation is to stand,
 * and the compensating transaction when it is not.
 */
function settleProvisionalWidths(
  oldState: EditorState,
  newState: EditorState
): Transaction | null {
  if (!dragEnded(oldState, newState)) {
    return null
  }
  const pending = materializeColumnWidthsPluginKey.getState(newState)
  if (!pending) {
    return null
  }

  const tr = newState.tr
    .setMeta(materializeColumnWidthsPluginKey, {
      pending: null,
    } satisfies ProvisionalWidthsMeta)
    // The materialisation was never in the history either, so undo must not
    // stop on the pair of them. A resize the user did keep is a single undo
    // step, made by `columnResizing`'s own transaction.
    .setMeta("addToHistory", false)

  const cells = resolveProvisionalCells(newState.doc, pending)
  // Whatever went unrecognised — the table gone, the cells no longer the ones
  // that were written, a width that has since changed — is either a resize to
  // keep or a document this plugin can no longer reason about. Leaving the
  // materialisation in place is the only safe answer to both.
  if (!cells) {
    return tr
  }

  for (const cell of cells) {
    tr.setNodeMarkup(cell.pos, null, {
      ...cell.attrs,
      colwidth: cell.restore,
    })
  }
  return tr
}

/** Whether these two states straddle the end of a column drag. */
function dragEnded(oldState: EditorState, newState: EditorState): boolean {
  const before = columnResizingPluginKey.getState(oldState)
  const after = columnResizingPluginKey.getState(newState)
  return !!before?.dragging && !after?.dragging
}

/** A pending cell re-resolved against the current document. */
type ResolvedProvisionalCell = {
  pos: number
  attrs: Attrs
  restore: unknown
}

/**
 * Re-resolve every pending cell, or `null` if any of them no longer is the cell
 * that was written with exactly the width that was written to it.
 *
 * This doubles as the test for whether the drag did anything. `columnResizing`
 * writes the final width of the dragged column through `updateColumnWidth` when
 * the drag ends, and skips the write when that width is the one the cell
 * already has, so the widths still matching the ones materialised on mousedown
 * is precisely "no column ended up a different size". A drag released at the
 * width it started from settles as the no-op it was, and a pointer that never
 * moved needs no tracking of its own.
 */
function resolveProvisionalCells(
  doc: ProseMirrorNode,
  pending: readonly ProvisionalCellWidth[]
): ResolvedProvisionalCell[] | null {
  const cells: ResolvedProvisionalCell[] = []
  for (const written of pending) {
    const node = doc.nodeAt(written.pos)
    if (!node || !isTableCell(node)) {
      return null
    }
    if (!colwidthEquals(node.attrs.colwidth, written.colwidth)) {
      return null
    }
    cells.push({
      pos: written.pos,
      attrs: node.attrs,
      restore: written.restore,
    })
  }
  return cells
}

function isTableCell(node: ProseMirrorNode): boolean {
  const role = node.type.spec.tableRole
  return role === "cell" || role === "header_cell"
}

function colwidthEquals(value: unknown, expected: readonly number[]): boolean {
  if (!Array.isArray(value) || value.length !== expected.length) {
    return false
  }
  return expected.every((width, index) => value[index] === width)
}

function materializeColumnWidths(view: EditorView): void {
  // Mirror `columnResizing`'s own mousedown guard exactly — an editable view, a
  // handle already armed by its `mousemove`, and no drag in flight — so widths
  // are materialised when a resize is about to begin and never otherwise.
  // Upstream inspects nothing else about the event, not even `button`, so
  // neither do we: any guard narrower than upstream's would let a drag start on
  // a table whose widths had not been materialised, which is the exact failure
  // this plugin exists to prevent.
  if (!view.editable) {
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
  const pending: ProvisionalCellWidth[] = []
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
      const colspan = readSpan(cell.attrs.colspan)
      const colwidth = columnWidths.slice(
        col,
        Math.min(col + colspan, map.width)
      )
      if (colwidth.length === 0) {
        continue
      }
      const pos = tableStart + cellPos
      tr.setNodeMarkup(pos, null, { ...cell.attrs, colwidth })
      pending.push({ pos, colwidth, restore: cell.attrs.colwidth })
    }
  }

  if (!tr.docChanged) {
    return
  }
  // Keep the resize a single undo step. The materialised widths are visually
  // identical to what the user was already looking at, so an undo entry for
  // them would read as a no-op press that has to be repeated.
  view.dispatch(
    tr
      .setMeta(materializeColumnWidthsPluginKey, {
        pending,
      } satisfies ProvisionalWidthsMeta)
      .setMeta("addToHistory", false)
  )
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
    const colspan = Math.min(readSpan(cell.attrs.colspan), map.width - col)
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
