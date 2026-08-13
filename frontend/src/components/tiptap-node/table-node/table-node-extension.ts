import type { TableOptions } from "@tiptap/extension-table"
import { Table, TableView } from "@tiptap/extension-table"
import type { Node as ProseMirrorNode } from "@tiptap/pm/model"
import type { EditorState } from "@tiptap/pm/state"
import type { TableRect } from "@tiptap/pm/tables"
import { moveTableColumn, selectedRect } from "@tiptap/pm/tables"
import type { DerivedColumnWidths } from "@/components/tiptap-node/table-node/table-column-widths"
import {
  applyColumnPercentages,
  applyDerivedColumnWidths,
  columnWeightsAreReordered,
  deriveColumnWeights,
} from "@/components/tiptap-node/table-node/table-column-widths"
import { renderTableMarkdown } from "@/components/tiptap-node/table-node/table-markdown"
import { createMaterializeColumnWidthsPlugin } from "@/components/tiptap-node/table-node/table-materialize-widths"

// Augmenting `@tiptap/react` rather than `@tiptap/core`: core is not a direct
// dependency, so its specifier does not resolve from this package. `@tiptap/react`
// re-exports it, and the augmentation merges into the same `Commands` interface
// the `Editor` type reads.
declare module "@tiptap/react" {
  interface Commands<ReturnType> {
    tracecatTable: {
      /**
       * Swap the column holding the selection with the one to its left.
       * @returns True when the column moved, false at the leftmost column.
       */
      moveTableColumnLeft: () => ReturnType
      /**
       * Swap the column holding the selection with the one to its right.
       * @returns True when the column moved, false at the rightmost column.
       */
      moveTableColumnRight: () => ReturnType
    }
  }
}

/**
 * The selected cell rectangle, or `null` when the selection is not in a table.
 *
 * `selectedRect` throws for a selection outside a table instead of reporting it,
 * and an exception thrown out of a command escapes `editor.can()`'s dry run
 * rather than disabling the button.
 */
function selectedTableRect(state: EditorState): TableRect | null {
  try {
    return selectedRect(state)
  } catch {
    return null
  }
}

/** Whether a resolved rectangle has a column to its left to swap with. */
function rectHasColumnLeft(rect: TableRect | null): rect is TableRect {
  return rect !== null && rect.left > 0
}

/** Whether a resolved rectangle has a column to its right to swap with. */
function rectHasColumnRight(rect: TableRect | null): rect is TableRect {
  // `right` is exclusive, so it equals the width at the last column.
  return rect !== null && rect.right < rect.map.width
}

/**
 * Whether {@link TracecatTable}'s `moveTableColumnLeft` would move a column.
 *
 * The toolbar asks through here rather than through `editor.can()`, which would
 * run the command for real — `moveColumn` transposes and rebuilds the entire
 * table node — on every transaction while the cursor sits in a table. This is
 * the same boundary rule the command itself applies, so the two cannot drift.
 * It is the command's only reason to refuse; `moveColumn` can still decline a
 * move it was asked to make, for a merged cell that spans both columns.
 */
export function canMoveTableColumnLeft(state: EditorState): boolean {
  return rectHasColumnLeft(selectedTableRect(state))
}

/**
 * Whether {@link TracecatTable}'s `moveTableColumnRight` would move a column.
 *
 * The mirror of {@link canMoveTableColumnLeft}; see it for why this exists.
 */
export function canMoveTableColumnRight(state: EditorState): boolean {
  return rectHasColumnRight(selectedTableRect(state))
}

/**
 * Upstream table node view that also applies derived proportional widths.
 *
 * `super` rebuilds the `<colgroup>` from the node's attributes on every update,
 * so the derived widths have to be written afterwards. The DOM shape is left
 * exactly as upstream builds it (`div.tableWrapper > table > colgroup + tbody`)
 * because prosemirror-tables walks up to the nearest `<table>` and treats its
 * first child as the colgroup.
 *
 * Widths are derived on structural change only — on mount, whenever the column
 * count moves, and whenever a column is moved — and merely re-applied for
 * content changes.
 */
export class TracecatTableView extends TableView {
  /**
   * The most recent derivation — the weights it measured and the percentages
   * they produced — or `null` when the last pass derived nothing (explicit
   * widths, or a malformed table).
   *
   * ProseMirror calls `update()` for every content change inside the table, so
   * applying freshly derived percentages there would visibly resize the columns
   * on every keystroke — the exact reflow this feature exists to remove.
   * Caching the percentages and replaying them keeps the columns still until
   * the table's shape actually changes. Note that the weights are re-measured
   * on every update regardless; that measurement is not what the cache avoids,
   * it is what tells a content edit apart from a structural change.
   */
  private derivedWidths: DerivedColumnWidths | null

  constructor(node: ProseMirrorNode, cellMinWidth: number) {
    super(node, cellMinWidth)
    this.derivedWidths = applyDerivedColumnWidths(
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
      this.derivedWidths = applyDerivedColumnWidths(
        this.node,
        this.colgroup,
        this.table
      )
    }
    return handled
  }

  /**
   * Replay the cached percentages, reporting whether they still describe the
   * table.
   *
   * Re-applying rather than skipping is required: `updateColumns` runs inside
   * `super.update()` and rewrites every `<col>` from the node's attrs, putting
   * the `min-width` for a width-less column back where it fights
   * `table-layout: fixed`.
   *
   * Returns false — asking the caller to derive afresh — for the two changes
   * the cached percentages cannot survive: a column inserted or deleted, which
   * changes the number of weights, and a column moved, which reorders them.
   * Everything else is a content edit, which deliberately leaves the columns
   * where they are.
   */
  private reapplyDerivedColumnWidths(): boolean {
    const cached = this.derivedWidths
    if (!cached) {
      return false
    }

    const weights = deriveColumnWeights(this.node)
    if (!weights || weights.length !== cached.weights.length) {
      return false
    }
    if (columnWeightsAreReordered(weights, cached.weights)) {
      return false
    }

    return applyColumnPercentages(
      this.node,
      this.colgroup,
      this.table,
      cached.percentages
    )
  }
}

/**
 * Table node with stable, sensibly proportioned columns and persisted widths.
 *
 * The node view is registered through `addNodeView` rather than the extension's
 * `View` option because that option is only forwarded to `columnResizing`,
 * which is not installed while resizing is disabled or the editor is read-only.
 * `addNodeView` covers editable and read-only surfaces alike, and it wins over
 * the plain `TableView` that `columnResizing` registers through its own plugin
 * props: ProseMirror resolves `nodeViews` from the view's direct props before
 * any plugin's, and Tiptap sets `addNodeView` results as direct props.
 *
 * The type arguments are load-bearing, not decoration. `extend` infers its
 * config type from the object literal as soon as the literal contains a
 * property with a concrete function type (`renderMarkdown` here), which drops
 * the contextual typing that `NodeConfig` provides — `addNodeView`'s parameter
 * becomes implicitly `any` and `this.parent` disappears. Naming the type
 * arguments turns inference off for the call and restores both.
 */
export const TracecatTable = Table.extend<TableOptions, unknown>({
  /**
   * Widths are only written out when the user has set them; see
   * `table-markdown.ts` for the full Markdown-versus-HTML contract.
   */
  renderMarkdown: renderTableMarkdown,

  addNodeView() {
    return ({ node }) => new TracecatTableView(node, this.options.cellMinWidth)
  },

  addCommands() {
    return {
      // The whole upstream table command set — `insertTable`, `addColumnBefore`,
      // `deleteRow` and the rest — lives here and the toolbar depends on it, so
      // it has to be carried over rather than replaced.
      ...this.parent?.(),

      // `moveTableColumn` moves the cells' attributes and content but rebuilds
      // each cell with the node type already in the destination slot, so
      // `colwidth` travels with the column while the header row stays headers.
      // Both commands return false rather than throwing when the column is
      // already against the edge, which is what makes `editor.can()` usable for
      // the buttons' disabled state.
      moveTableColumnLeft:
        () =>
        ({ state, dispatch }) => {
          const rect = selectedTableRect(state)
          if (!rectHasColumnLeft(rect)) {
            return false
          }
          return moveTableColumn({ from: rect.left, to: rect.left - 1 })(
            state,
            dispatch
          )
        },

      moveTableColumnRight:
        () =>
        ({ state, dispatch }) => {
          const rect = selectedTableRect(state)
          if (!rectHasColumnRight(rect)) {
            return false
          }
          return moveTableColumn({ from: rect.right - 1, to: rect.right })(
            state,
            dispatch
          )
        },
    }
  },

  addProseMirrorPlugins() {
    // Ordering is load-bearing: the parent returns `[columnResizing?,
    // tableEditing]`, and materialisation has to run before `columnResizing`
    // sees the same mousedown. ProseMirror walks `handleDOMEvents` in plugin
    // order and stops at the first handler that returns true; ours returns
    // false.
    return [createMaterializeColumnWidthsPlugin(), ...(this.parent?.() ?? [])]
  },
})
