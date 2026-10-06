"use client"

import type React from "react"
import { createContext, useCallback, useContext, useState } from "react"

/** What the cell panel shows: a read-only view or an editor, per value kind. */
export type TablePanelMode =
  | "view-json"
  | "view-text"
  | "edit-text"
  | "edit-json"

/** Fallback panel heading per mode, for hosts with nothing more specific. */
export const TABLE_PANEL_TITLES: Record<TablePanelMode, string> = {
  "view-json": "View JSON",
  "view-text": "View text",
  "edit-text": "Edit text",
  "edit-json": "Edit JSON",
}

interface TablePanelContent {
  mode: TablePanelMode
  value: unknown
  onSave?: (value: unknown) => void
  /** Name of the column the value belongs to, for hosts that title the panel. */
  title?: string
}

interface TablePanelContextValue {
  panelOpen: boolean
  panelContent: TablePanelContent | null
  openPanel: (content: TablePanelContent) => void
  closePanel: () => void
}

const TablePanelContext = createContext<TablePanelContextValue | null>(null)

export function useTablePanel() {
  const context = useContext(TablePanelContext)
  if (!context) {
    throw new Error("useTablePanel must be used within TablePanelProvider")
  }
  return context
}

export function TablePanelProvider({
  children,
}: {
  children: React.ReactNode
}) {
  const [panelOpen, setPanelOpen] = useState(false)
  const [panelContent, setPanelContent] = useState<TablePanelContent | null>(
    null
  )

  const openPanel = useCallback((content: TablePanelContent) => {
    setPanelContent(content)
    setPanelOpen(true)
  }, [])

  const closePanel = useCallback(() => {
    setPanelOpen(false)
    setPanelContent(null)
  }, [])

  return (
    <TablePanelContext.Provider
      value={{ panelOpen, panelContent, openPanel, closePanel }}
    >
      {children}
    </TablePanelContext.Provider>
  )
}
