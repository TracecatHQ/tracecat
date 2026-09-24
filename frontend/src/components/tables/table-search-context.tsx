"use client"

import { createContext, type ReactNode, useContext } from "react"
import { useTableSearch } from "@/hooks/use-table-search"
import { useWorkspaceId } from "@/providers/workspace-id"

const TableSearchContext = createContext<ReturnType<
  typeof useTableSearch
> | null>(null)

/** Share one status poll and mutation state across every column in this table. */
export function TableSearchProvider({
  tableId,
  children,
}: {
  tableId: string
  children: ReactNode
}) {
  const workspaceId = useWorkspaceId()
  const value = useTableSearch(workspaceId, tableId)
  return (
    <TableSearchContext.Provider value={value}>
      {children}
    </TableSearchContext.Provider>
  )
}

/** Read optional table search controls without affecting other grid consumers. */
export function useTableSearchContext() {
  return useContext(TableSearchContext)
}
