"use client"

import type React from "react"
import { createContext, useCallback, useContext, useState } from "react"

type TablePanelMode = "view-json" | "edit-text" | "edit-json"

interface TablePanelContent {
  mode: TablePanelMode
  value: unknown
  onSave?: (value: unknown) => void
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
