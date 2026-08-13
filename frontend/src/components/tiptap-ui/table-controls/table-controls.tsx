"use client"

import type { Editor } from "@tiptap/react"
// --- Icons ---
import { Table as TableIcon } from "lucide-react"
// --- Tiptap UI ---
import type { TableButton } from "@/components/tiptap-ui/table-controls"
import { useInsertTable } from "@/components/tiptap-ui/table-controls"
// --- UI Primitives ---
import { Button, ButtonGroup } from "@/components/tiptap-ui-primitive/button"
import { ToolbarGroup } from "@/components/tiptap-ui-primitive/toolbar"

export interface TableInsertButtonProps {
  /**
   * Optional editor instance. Falls back to the editor from context.
   */
  editor?: Editor | null
}

/**
 * Toolbar button that inserts a 3x2 table with a header row.
 */
export function TableInsertButton({ editor }: TableInsertButtonProps) {
  const { canInsertTable, insertTable } = useInsertTable(editor)

  return (
    <Button
      type="button"
      data-style="ghost"
      data-disabled={!canInsertTable}
      disabled={!canInsertTable}
      tooltip="Insert table"
      aria-label="Insert table"
      onClick={insertTable}
    >
      <TableIcon className="tiptap-button-icon" />
    </Button>
  )
}

export interface TableControlsGroupProps {
  /**
   * Table toolbar buttons to render, from `useTableControls`.
   */
  buttons: TableButton[]
}

/**
 * Toolbar group rendering a set of table row/column buttons.
 */
export function TableControlsGroup({ buttons }: TableControlsGroupProps) {
  return (
    <ToolbarGroup className="simple-editor-table-controls">
      <ButtonGroup orientation="horizontal">
        {buttons.map((button) => (
          <Button
            key={button.key}
            type="button"
            data-style="ghost"
            data-disabled={button.disabled}
            disabled={button.disabled}
            tooltip={button.tooltip}
            aria-label={button.tooltip}
            onClick={button.onClick}
          >
            {button.icon}
          </Button>
        ))}
      </ButtonGroup>
    </ToolbarGroup>
  )
}
