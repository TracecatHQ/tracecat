"use client"
import { useQuery } from "@tanstack/react-query"
import type { ReactNode } from "react"
import { workspacesGetWorkspace } from "@/client"
import { retryHandler } from "@/lib/errors"
import { WorkspaceIdProvider } from "./workspace-id"

export function WorkspaceProvider({
  workspaceId,
  children,
}: {
  workspaceId: string
  children: ReactNode
}) {
  useQuery({
    queryKey: ["workspace", workspaceId],
    queryFn: () => workspacesGetWorkspace({ workspaceId }),
    retry: retryHandler,
    staleTime: 300_000, // 5 min
  })
  return (
    <WorkspaceIdProvider value={workspaceId}>{children}</WorkspaceIdProvider>
  )
}
