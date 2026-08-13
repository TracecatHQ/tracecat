import { Table, TableView } from "@tiptap/extension-table"
import type { Node as ProseMirrorNode } from "@tiptap/pm/model"
import {
  applyColumnPercentages,
  applyDerivedColumnWidths,
  tableColumnCount,
} from "@/components/tiptap-node/table-node/table-column-widths"

/**
 * Upstream table node view that also applies derived proportional widths.
 *
 * `super` rebuilds the `<colgroup>` from the node's attributes on every update,
 * so the derived widths have to be written afterwards. The DOM shape is left
 * exactly as upstream builds it (`div.tableWrapper > table > colgroup + tbody`)
 * because prosemirror-tables walks up to the nearest `<table>` and treats its
 * first child as the colgroup.
 *
 * Widths are derived on structural change only — on mount and whenever the
 * column count moves — and merely re-applied for content changes.
 */
export class TracecatTableView extends TableView {
  /**
   * Percentages written by the most recent derivation, or `null` when the last
   * pass derived nothing (explicit widths, or a malformed table).
   *
   * ProseMirror calls `update()` for every content change inside the table, so
   * deriving there would re-measure the cell text as the user types and the
   * columns would visibly resize on every keystroke — the exact reflow this
   * feature exists to remove. Caching the derivation and replaying it keeps the
   * columns still until the table's shape actually changes.
   */
  private derivedPercentages: number[] | null

  constructor(node: ProseMirrorNode, cellMinWidth: number) {
    super(node, cellMinWidth)
    this.derivedPercentages = applyDerivedColumnWidths(
      node,
      this.colgroup,
      this.table
    )
  }

  update(node: ProseMirrorNode): boolean {
    const handled = super.update(node)
    if (!handled) {
      return handled
    }

    if (!this.reapplyDerivedColumnWidths()) {
      this.derivedPercentages = applyDerivedColumnWidths(
        this.node,
        this.colgroup,
        this.table
      )
    }
    return handled
  }

  /**
   * Replay the cached percentages, reporting whether they still fit the table.
   *
   * Re-applying rather than skipping is required: `updateColumns` runs inside
   * `super.update()` and rewrites every `<col>` from the node's attrs, putting
   * the `min-width` for a width-less column back where it fights
   * `table-layout: fixed`.
   */
  private reapplyDerivedColumnWidths(): boolean {
    const cached = this.derivedPercentages
    if (!cached || tableColumnCount(this.node) !== cached.length) {
      return false
    }
    return applyColumnPercentages(this.node, this.colgroup, this.table, cached)
  }
}

/**
 * Table node with stable, sensibly proportioned columns.
 *
 * The node view is registered through `addNodeView` rather than the extension's
 * `View` option because that option is only forwarded to `columnResizing`,
 * which is not installed while resizing is disabled or the editor is read-only.
 * `addNodeView` covers editable and read-only surfaces alike.
 */
export const TracecatTable = Table.extend({
  addNodeView() {
    return ({ node }) => new TracecatTableView(node, this.options.cellMinWidth)
  },
})
