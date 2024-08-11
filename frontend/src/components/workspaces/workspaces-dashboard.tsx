"use client"

import Link from "next/link"
import { WorkspaceMetadataResponse } from "@/client"
import { useAuth } from "@/providers/auth"

import { useWorkspaces } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { AlertNotification } from "@/components/notifications"
import { ListItemSkeletion } from "@/components/skeletons"
import { WorkspaceManagementButton } from "@/components/workspaces/workspaces-admin-button"

export function WorkspacesDashboard() {
  const { user } = useAuth()
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[800px] flex-col space-y-12 p-16 pt-32">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-bold tracking-tight">Workspaces</h2>
            <p className="text-md text-muted-foreground">
              You're a member of these workspaces.
            </p>
            <p className="text-md text-muted-foreground">
              You're a {user?.role} user
            </p>
          </div>
          <div className="ml-auto flex items-center space-x-2">
            {user?.role === "admin" && <WorkspaceManagementButton />}
          </div>
        </div>
        <WorkspacesList />
      </div>
    </div>
  )
}

export function WorkspacesList() {
  const { data: workspaces, error, isLoading } = useWorkspaces()
  if (isLoading) {
    return (
      <div className="flex w-full flex-col items-center space-y-12">
        <ListItemSkeletion n={2} />
      </div>
    )
  }
  if (error || workspaces === undefined) {
    return (
      <AlertNotification level="error" message="Error fetching workflows" />
    )
  }

  return (
    <div className="flex flex-col space-y-4">
      {workspaces.length === 0 ? (
        <div className="flex w-full flex-col items-center space-y-12">
          <ListItemSkeletion n={2} />
          <div className="space-y-4 text-center">
            <p className="text-sm">Welcome to Tracecat 👋</p>
          </div>
        </div>
      ) : (
        <>
          {workspaces.map((ws, idx) => (
            <WorkspaceItem key={idx} workspace={ws} />
          ))}
        </>
      )}
    </div>
  )
}

export function WorkspaceItem({
  workspace,
}: {
  workspace: WorkspaceMetadataResponse
}) {
  return (
    <Link
      key={workspace.id}
      href={`/workspaces/${workspace.id}`}
      className={cn(
        "flex min-h-24 min-w-[600px] flex-col items-start justify-start rounded-lg border p-6 text-left text-sm shadow-md transition-all hover:bg-accent",
        "dark:bg-muted dark:text-white dark:hover:bg-muted dark:hover:text-white"
      )}
    >
      <div className="flex w-full flex-col gap-1">
        <div className="flex items-center">
          <div className="flex items-center gap-2">
            <div className="font-semibold">{workspace.name}</div>
          </div>
          <div className="ml-auto flex items-center space-x-2">
            <span className="text-xs text-muted-foreground">
              {workspace.n_members} members
            </span>
          </div>
        </div>
        <div className="text-xs font-medium text-muted-foreground">
          {workspace.id}
        </div>
      </div>
    </Link>
  )
}
