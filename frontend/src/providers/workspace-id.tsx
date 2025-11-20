"use client"
import { createContext, type ReactNode, useContext } from "react"

const WorkspaceIdContext = createContext<string | undefined>(undefined)

export function WorkspaceIdProvider({
  workspaceId,
  children,
}: {
  workspaceId: string
  children: ReactNode
}) {
  return (
    <WorkspaceIdContext.Provider value={workspaceId}>
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
