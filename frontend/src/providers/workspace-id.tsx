"use client"
import { createContext, type ReactNode, useContext } from "react"

const WorkspaceIdContext = createContext<string | undefined>(undefined)

export function WorkspaceIdProvider({
  value,
  children,
}: {
  value: string
  children: ReactNode
}) {
  return (
    <WorkspaceIdContext.Provider value={value}>
      {children}
    </WorkspaceIdContext.Provider>
  )
}

export function useWorkspaceId() {
  const id = useContext(WorkspaceIdContext)
  if (id === undefined) {
    throw new Error("useWorkspaceId must be used within a WorkspaceIdProvider")
  }
  return id
}
