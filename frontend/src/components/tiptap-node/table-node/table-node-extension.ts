import { Table } from "@tiptap/extension-table"
import type { EditorState } from "@tiptap/pm/state"
import type { TableRect } from "@tiptap/pm/tables"
import { moveTableColumn, selectedRect } from "@tiptap/pm/tables"

// Augmenting `@tiptap/react` rather than `@tiptap/core`: core is not a direct
// dependency, so its specifier does not resolve from this package.
// `@tiptap/react` re-exports it, and the augmentation merges into the same
// `Commands` interface the `Editor` type reads.
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
 * `selectedRect` throws for a selection outside a table instead of reporting
 * it, and an exception thrown out of a command escapes `editor.can()`'s dry run
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
 * run the command for real — `moveTableColumn` transposes and rebuilds the
 * entire table node — on every transaction while the cursor sits in a table.
 * This is the same boundary rule the command itself applies, so the two cannot
 * drift. It is the command's only reason to refuse; `moveTableColumn` can still
 * decline a move it was asked to make, for a merged cell spanning both columns.
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
 * The upstream table extension plus the two move-column commands.
 */
export const TracecatTable = Table.extend({
  addCommands() {
    return {
      // The whole upstream table command set — `insertTable`,
      // `addColumnBefore`, `deleteRow` and the rest — lives here and the
      // toolbar depends on it, so it has to be carried over rather than
      // replaced.
      ...this.parent?.(),

      // `moveTableColumn` moves the cells' attributes and content but rebuilds
      // each cell with the node type already in the destination slot, so
      // `colwidth` travels with the column while the header row stays headers.
      // Both commands return false rather than throwing when the column is
      // already against the edge.
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
})
