"use client"

import type { Editor } from "@tiptap/react"
// Panel icons below are chosen for the direction their arrow points, not for
// the panel edge in their name. In lucide, `PanelLeftOpen` draws a
// right-pointing arrow, `PanelRightOpen` a left-pointing one, `PanelTopOpen`
// points down and `PanelBottomOpen` points up. Users read the arrow, so the
// name-to-command pairing looks inverted on purpose. Do not "fix" it.
import {
  ArrowLeftToLine,
  ArrowRightToLine,
  BookmarkX,
  Delete as DeleteIcon,
  PanelBottomOpen,
  PanelLeftOpen,
  PanelRightOpen,
  PanelTopOpen,
  Trash2,
} from "lucide-react"
import * as React from "react"
import {
  canMoveTableColumnLeft,
  canMoveTableColumnRight,
} from "@/components/tiptap-node/table-node/table-node-extension"
// --- Hooks ---
import { useTiptapEditor } from "@/hooks/use-tiptap-editor"

/**
 * Descriptor for a single table toolbar button.
 */
export type TableButton = {
  key: string
  tooltip: string
  disabled: boolean
  onClick: () => void
  icon: React.ReactNode
}

/**
 * Table toolbar buttons split into the insert and delete groups.
 */
export interface TableButtonGroups {
  insertButtons: TableButton[]
  moveButtons: TableButton[]
  deleteButtons: TableButton[]
}

/** The no-buttons result, shared by every path that renders nothing. */
function emptyTableButtonGroups(): TableButtonGroups {
  return { insertButtons: [], moveButtons: [], deleteButtons: [] }
}

/**
 * Builds the row/column table toolbar buttons for the current editor state.
 *
 * Every group is empty when the editor is not editable, or when the cursor is
 * not inside a table. This is the only place the editable check lives; callers
 * pass the editor straight through.
 */
export function getTableButtonGroups(
  editor: Editor,
  isTableActive: boolean
): TableButtonGroups {
  if (!editor.isEditable) {
    return emptyTableButtonGroups()
  }

  const insertButtons: TableButton[] = []

  if (isTableActive) {
    insertButtons.push(
      {
        key: "add-column-before",
        tooltip: "Insert column to the left",
        disabled: !editor.can().addColumnBefore(),
        onClick: () => editor.chain().focus().addColumnBefore().run(),
        icon: <PanelRightOpen className="tiptap-button-icon" />,
      },
      {
        key: "add-column-after",
        tooltip: "Insert column to the right",
        disabled: !editor.can().addColumnAfter(),
        onClick: () => editor.chain().focus().addColumnAfter().run(),
        icon: <PanelLeftOpen className="tiptap-button-icon" />,
      },
      {
        key: "add-row-before",
        tooltip: "Insert row above",
        disabled: !editor.can().addRowBefore(),
        onClick: () => editor.chain().focus().addRowBefore().run(),
        icon: <PanelBottomOpen className="tiptap-button-icon" />,
      },
      {
        key: "add-row-after",
        tooltip: "Insert row below",
        disabled: !editor.can().addRowAfter(),
        onClick: () => editor.chain().focus().addRowAfter().run(),
        icon: <PanelTopOpen className="tiptap-button-icon" />,
      }
    )
  }

  // The other buttons ask `editor.can()`, which runs their command for real
  // against a throwaway state. That is cheap for every command here except the
  // two moves: `moveColumn` transposes and rebuilds the whole table node, and
  // this runs on every transaction while the cursor is in a table, so the two
  // moves ask the shared boundary rule directly instead.
  const moveButtons: TableButton[] = isTableActive
    ? [
        {
          key: "move-column-left",
          tooltip: "Move column left",
          disabled: !canMoveTableColumnLeft(editor.state),
          onClick: () => editor.chain().focus().moveTableColumnLeft().run(),
          icon: <ArrowLeftToLine className="tiptap-button-icon" />,
        },
        {
          key: "move-column-right",
          tooltip: "Move column right",
          disabled: !canMoveTableColumnRight(editor.state),
          onClick: () => editor.chain().focus().moveTableColumnRight().run(),
          icon: <ArrowRightToLine className="tiptap-button-icon" />,
        },
      ]
    : []

  const deleteButtons: TableButton[] = isTableActive
    ? [
        {
          key: "delete-column",
          tooltip: "Delete column",
          disabled: !editor.can().deleteColumn(),
          onClick: () => editor.chain().focus().deleteColumn().run(),
          icon: <BookmarkX className="tiptap-button-icon" />,
        },
        {
          key: "delete-row",
          tooltip: "Delete row",
          disabled: !editor.can().deleteRow(),
          onClick: () => editor.chain().focus().deleteRow().run(),
          icon: <DeleteIcon className="tiptap-button-icon" />,
        },
        {
          key: "delete-table",
          tooltip: "Delete table",
          disabled: !editor.can().deleteTable(),
          onClick: () => editor.chain().focus().deleteTable().run(),
          icon: <Trash2 className="tiptap-button-icon" />,
        },
      ]
    : []

  return { insertButtons, moveButtons, deleteButtons }
}

/** Resolves whether the insert-table command is available for the selection. */
function resolveCanInsertTable(editor: Editor | null): boolean {
  if (!editor || !editor.isEditable) {
    return false
  }

  const can = editor.can()
  // No command, no insert: reporting "available" here would only enable a
  // button whose click cannot do anything.
  if (typeof can.insertTable !== "function") {
    return false
  }

  return can.insertTable({ rows: 3, cols: 2, withHeaderRow: true })
}

/**
 * Provides the insert-table command and whether it is currently available.
 *
 * @param providedEditor - Optional editor instance, defaults to the context editor.
 */
export function useInsertTable(providedEditor?: Editor | null): {
  canInsertTable: boolean
  insertTable: () => void
} {
  const { editor } = useTiptapEditor(providedEditor)

  const insertTable = React.useCallback(() => {
    if (!editor || !editor.isEditable) {
      return
    }

    editor
      .chain()
      .focus()
      .insertTable({ rows: 3, cols: 2, withHeaderRow: true })
      .run()
  }, [editor])

  // Deliberately computed during render instead of memoized on `editor`: the
  // editor instance is stable for its whole lifetime, so a memo keyed on it
  // would run once and the disabled state would never follow the selection.
  // `useTiptapEditor` subscribes to `editorState`, which changes on every
  // transaction and re-renders us, and `editor.can()` is cheap.
  const canInsertTable = resolveCanInsertTable(editor)

  return { canInsertTable, insertTable }
}

/**
 * Reports whether the cursor is currently inside a table.
 *
 * Recomputed on every render so it follows the selection; `useTiptapEditor`
 * re-renders callers on each editor transaction.
 *
 * @param providedEditor - Optional editor instance, defaults to the context editor.
 */
export function useIsInTable(providedEditor?: Editor | null): boolean {
  const { editor } = useTiptapEditor(providedEditor)
  return !!editor?.isActive("table")
}

/**
 * Provides the table row/column toolbar button groups for the current editor.
 *
 * @param providedEditor - Optional editor instance, defaults to the context editor.
 */
export function useTableControls(
  providedEditor?: Editor | null
): TableButtonGroups {
  const { editor } = useTiptapEditor(providedEditor)
  const isTableActive = useIsInTable(editor)

  // Deliberately computed during render, for the same reason as
  // `useInsertTable` above. Every input a memo could key on is stable while the
  // cursor stays inside one table — `editor` for its whole lifetime, and
  // `isTableActive` until the cursor leaves — so the `editor.can()` flags would
  // freeze at the moment the cursor entered the table and stop following the
  // caret between columns. Do not re-memoize this.
  if (!editor) {
    return emptyTableButtonGroups()
  }
  return getTableButtonGroups(editor, isTableActive)
}
