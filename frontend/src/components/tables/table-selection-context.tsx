"use client"

import type { GridApi } from "ag-grid-community"
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
} from "react"
import type { TableColumnRead } from "@/client"

type TableSelectionState = {
  selectedCount: number
  selectedRowIds: string[]
  gridApi: GridApi | null
  tableId: string
  columns: TableColumnRead[]
}

type TableSelectionContextValue = TableSelectionState & {
  updateSelection: (state: Partial<TableSelectionState>) => void
  resetSelection: () => void
}

const initialState: TableSelectionState = {
  selectedCount: 0,
  selectedRowIds: [],
  gridApi: null,
  tableId: "",
  columns: [],
}

const defaultContextValue: TableSelectionContextValue = {
  ...initialState,
  updateSelection: () => {},
  resetSelection: () => {},
}

const TableSelectionContext =
  createContext<TableSelectionContextValue>(defaultContextValue)

export function TableSelectionProvider({ children }: React.PropsWithChildren) {
  const [state, setState] = useState<TableSelectionState>(initialState)

  const updateSelection = useCallback((next: Partial<TableSelectionState>) => {
    setState((prev) => ({ ...prev, ...next }))
  }, [])

  const resetSelection = useCallback(() => {
    setState(() => ({ ...initialState }))
  }, [])

  const value = useMemo<TableSelectionContextValue>(
    () => ({
      ...state,
      updateSelection,
      resetSelection,
    }),
    [state, updateSelection, resetSelection]
  )

  return (
    <TableSelectionContext.Provider value={value}>
      {children}
    </TableSelectionContext.Provider>
  )
}

export function useTableSelection() {
  return useContext(TableSelectionContext)
}
