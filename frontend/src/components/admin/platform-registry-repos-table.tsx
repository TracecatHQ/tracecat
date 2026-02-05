"use client"

import { DotsHorizontalIcon, ReloadIcon } from "@radix-ui/react-icons"
import { useState } from "react"
import type { RepositoryStatus } from "@/client"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"
import { useAdminRegistryStatus, useAdminRegistrySync } from "@/hooks/use-admin"
import { getRelativeTime } from "@/lib/event-history"

export function PlatformRegistryReposTable() {
  const [syncingId, setSyncingId] = useState<string | null>(null)
  const { status, isLoading, refetch } = useAdminRegistryStatus()
  const { syncRepository, syncPending } = useAdminRegistrySync()

  const handleSync = async (repositoryId: string, force = false) => {
    setSyncingId(repositoryId)
    try {
      await syncRepository({ repositoryId, force })
      toast({
        title: "Sync started",
        description: "Repository sync has been initiated.",
      })
      refetch()
    } catch (error) {
      console.error("Failed to sync repository", error)
      toast({
        title: "Sync failed",
        description: "Failed to sync repository. Please try again.",
        variant: "destructive",
      })
    } finally {
      setSyncingId(null)
    }
  }

  if (isLoading) {
    return <div className="text-muted-foreground">Loading repositories...</div>
  }

  const repositories = status?.repositories ?? []

  return (
    <DataTable
      data={repositories}
      initialSortingState={[{ id: "name", desc: false }]}
      columns={[
        {
          accessorKey: "name",
          header: ({ column }) => (
            <DataTableColumnHeader
              className="text-xs"
              column={column}
              title="Name"
            />
          ),
          cell: ({ row }) => (
            <div className="text-xs font-medium">
              {row.getValue<RepositoryStatus["name"]>("name")}
            </div>
          ),
          enableSorting: true,
          enableHiding: false,
        },
        {
          accessorKey: "origin",
          header: ({ column }) => (
            <DataTableColumnHeader
              className="text-xs"
              column={column}
              title="Origin"
            />
          ),
          cell: ({ row }) => (
            <div className="text-xs font-mono text-muted-foreground">
              {row.getValue<RepositoryStatus["origin"]>("origin")}
            </div>
          ),
          enableSorting: true,
          enableHiding: false,
        },
        {
          accessorKey: "commit_sha",
          header: ({ column }) => (
            <DataTableColumnHeader
              className="text-xs"
              column={column}
              title="Version"
            />
          ),
          cell: ({ row }) => {
            const commitSha =
              row.getValue<RepositoryStatus["commit_sha"]>("commit_sha")
            return (
              <div className="text-xs">
                {commitSha ? (
                  <Badge variant="outline" className="font-mono">
                    {commitSha.substring(0, 7)}
                  </Badge>
                ) : (
                  <span className="text-muted-foreground">Not synced</span>
                )}
              </div>
            )
          },
          enableSorting: false,
          enableHiding: false,
        },
        {
          accessorKey: "last_synced_at",
          header: ({ column }) => (
            <DataTableColumnHeader
              className="text-xs"
              column={column}
              title="Last synced"
            />
          ),
          cell: ({ row }) => {
            const lastSynced =
              row.getValue<RepositoryStatus["last_synced_at"]>("last_synced_at")
            if (!lastSynced) {
              return <div className="text-xs text-muted-foreground">Never</div>
            }
            const date = new Date(lastSynced)
            const ago = getRelativeTime(date)
            return (
              <div className="text-xs text-muted-foreground">
                <span>{date.toLocaleDateString()}</span>
                <span className="ml-1">({ago})</span>
              </div>
            )
          },
          enableSorting: true,
          enableHiding: false,
        },
        {
          id: "actions",
          enableHiding: false,
          cell: ({ row }) => {
            const repo = row.original
            const isSyncing = syncingId === repo.id && syncPending

            return (
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="ghost" className="size-8 p-0">
                    <span className="sr-only">Open menu</span>
                    {isSyncing ? (
                      <ReloadIcon className="size-4 animate-spin" />
                    ) : (
                      <DotsHorizontalIcon className="size-4" />
                    )}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem
                    onClick={() => navigator.clipboard.writeText(repo.id)}
                  >
                    Copy ID
                  </DropdownMenuItem>
                  <DropdownMenuSeparator />
                  <DropdownMenuItem
                    onClick={() => handleSync(repo.id, false)}
                    disabled={isSyncing}
                  >
                    Sync repository
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    onClick={() => handleSync(repo.id, true)}
                    disabled={isSyncing}
                  >
                    Force sync (delete existing)
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            )
          },
        },
      ]}
      toolbarProps={defaultToolbarProps}
    />
  )
}

const defaultToolbarProps: DataTableToolbarProps<RepositoryStatus> = {
  filterProps: {
    placeholder: "Filter repositories...",
    column: "name",
  },
}
