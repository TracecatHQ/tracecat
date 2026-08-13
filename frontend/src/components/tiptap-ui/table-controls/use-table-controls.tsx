"use client"

import type { Editor } from "@tiptap/react"
import {
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
    return { insertButtons: [], deleteButtons: [] }
  }

  const insertButtons: TableButton[] = []

  if (isTableActive) {
    insertButtons.push(
      {
        key: "add-column-before",
        tooltip: "Insert column to the left",
        disabled: !editor.can().addColumnBefore(),
        onClick: () => editor.chain().focus().addColumnBefore().run(),
        icon: <PanelLeftOpen className="tiptap-button-icon" />,
      },
      {
        key: "add-column-after",
        tooltip: "Insert column to the right",
        disabled: !editor.can().addColumnAfter(),
        onClick: () => editor.chain().focus().addColumnAfter().run(),
        icon: <PanelRightOpen className="tiptap-button-icon" />,
      },
      {
        key: "add-row-before",
        tooltip: "Insert row above",
        disabled: !editor.can().addRowBefore(),
        onClick: () => editor.chain().focus().addRowBefore().run(),
        icon: <PanelTopOpen className="tiptap-button-icon" />,
      },
      {
        key: "add-row-after",
        tooltip: "Insert row below",
        disabled: !editor.can().addRowAfter(),
        onClick: () => editor.chain().focus().addRowAfter().run(),
        icon: <PanelBottomOpen className="tiptap-button-icon" />,
      }
    )
  }

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

  return { insertButtons, deleteButtons }
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

  const canInsertTable = React.useMemo(() => {
    if (!editor || !editor.isEditable) {
      return false
    }

    const can = editor.can()
    if (typeof can.insertTable !== "function") {
      return editor.isEditable
    }

    return can.insertTable({ rows: 3, cols: 2, withHeaderRow: true })
  }, [editor])

  return { canInsertTable, insertTable }
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
  const isTableActive = !!editor && editor.isActive("table")

  return React.useMemo<TableButtonGroups>(() => {
    if (!editor || !hasEditableEditor) {
      return { insertButtons: [], deleteButtons: [] }
    }
    return getTableButtonGroups(editor, isTableActive)
  }, [editor, hasEditableEditor, isTableActive])
}
