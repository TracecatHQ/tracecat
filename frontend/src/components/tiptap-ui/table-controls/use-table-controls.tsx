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

/**
 * Builds the row/column table toolbar buttons for the current editor state.
 *
 * Both groups are empty when the editor is not editable, or when the cursor is
 * not inside a table.
 */
export function getTableButtonGroups(
  editor: Editor,
  isTableActive: boolean
): TableButtonGroups {
  if (!editor.isEditable) {
    return { insertButtons: [], moveButtons: [], deleteButtons: [] }
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

  const moveButtons: TableButton[] = isTableActive
    ? [
        {
          key: "move-column-left",
          tooltip: "Move column left",
          disabled: !editor.can().moveTableColumnLeft(),
          onClick: () => editor.chain().focus().moveTableColumnLeft().run(),
          icon: <ArrowLeftToLine className="tiptap-button-icon" />,
        },
        {
          key: "move-column-right",
          tooltip: "Move column right",
          disabled: !editor.can().moveTableColumnRight(),
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
  if (typeof can.insertTable !== "function") {
    return editor.isEditable
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
  const hasEditableEditor = !!editor && editor.isEditable
  const isTableActive = useIsInTable(editor)

  return React.useMemo<TableButtonGroups>(() => {
    if (!editor || !hasEditableEditor) {
      return { insertButtons: [], moveButtons: [], deleteButtons: [] }
    }
    return getTableButtonGroups(editor, isTableActive)
  }, [editor, hasEditableEditor, isTableActive])
}
