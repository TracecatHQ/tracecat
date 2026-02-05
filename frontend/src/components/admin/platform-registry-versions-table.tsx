"use client"

import { CheckIcon } from "lucide-react"
import { useState } from "react"
import type { tracecat__admin__registry__schemas__RegistryVersionRead } from "@/client"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import {
  useAdminRegistryStatus,
  useAdminRegistrySync,
  useAdminRegistryVersions,
} from "@/hooks/use-admin"
import { getRelativeTime } from "@/lib/event-history"

type RegistryVersionRead =
  tracecat__admin__registry__schemas__RegistryVersionRead

export function PlatformRegistryVersionsTable() {
  const [promotingId, setPromotingId] = useState<string | null>(null)
  const { versions, isLoading } = useAdminRegistryVersions()
  const { status } = useAdminRegistryStatus()
  const { promoteVersion, promotePending } = useAdminRegistrySync()

  const handlePromote = async (version: RegistryVersionRead) => {
    setPromotingId(version.id)
    try {
      await promoteVersion({
        repositoryId: version.repository_id,
        versionId: version.id,
      })
      toast({
        title: "Version promoted",
        description: `Version ${version.version} is now the current version.`,
      })
    } catch (error) {
      console.error("Failed to promote version", error)
      toast({
        title: "Failed to promote version",
        description: "Please try again.",
        variant: "destructive",
      })
    } finally {
      setPromotingId(null)
    }
  }

  // Create a map of repository_id to current_version_id from status
  const currentVersionMap = new Map<string, string | null | undefined>()
  status?.repositories?.forEach((repo) => {
    currentVersionMap.set(repo.id, repo.current_version_id)
  })

  if (isLoading) {
    return <div className="text-muted-foreground">Loading versions...</div>
  }

  return (
    <DataTable
      data={versions ?? []}
      initialSortingState={[{ id: "created_at", desc: true }]}
      columns={[
        {
          accessorKey: "version",
          header: ({ column }) => (
            <DataTableColumnHeader
              className="text-xs"
              column={column}
              title="Version"
            />
          ),
          cell: ({ row }) => {
            const version = row.original
            const isCurrent =
              currentVersionMap.get(version.repository_id) === version.id
            return (
              <div className="flex items-center gap-2">
                <span className="text-xs font-mono">{version.version}</span>
                {isCurrent && (
                  <span className="flex items-center">
                    <span className="sr-only">Current</span>
                    <span
                      aria-hidden="true"
                      className="flex size-1.5 rounded-full bg-[hsl(var(--success)/0.55)]"
                    />
                  </span>
                )}
              </div>
            )
          },
          enableSorting: true,
          enableHiding: false,
        },
        {
          accessorKey: "repository_id",
          header: ({ column }) => (
            <DataTableColumnHeader
              className="text-xs"
              column={column}
              title="Repository"
            />
          ),
          cell: ({ row }) => {
            const repoId = row.getValue<string>("repository_id")
            const repo = status?.repositories?.find((r) => r.id === repoId)
            return (
              <div className="text-xs text-muted-foreground">
                {repo?.name ?? repoId.substring(0, 8)}
              </div>
            )
          },
          enableSorting: true,
          enableHiding: false,
        },
        {
          accessorKey: "commit_sha",
          header: ({ column }) => (
            <DataTableColumnHeader
              className="text-xs"
              column={column}
              title="Commit"
            />
          ),
          cell: ({ row }) => (
            <code className="text-xs text-muted-foreground">
              {row.getValue<string | null>("commit_sha")?.substring(0, 7) ??
                "-"}
            </code>
          ),
          enableSorting: false,
          enableHiding: true,
        },
        {
          accessorKey: "created_at",
          header: ({ column }) => (
            <DataTableColumnHeader
              className="text-xs"
              column={column}
              title="Created"
            />
          ),
          cell: ({ row }) => {
            const createdAt = row.getValue<string>("created_at")
            const date = new Date(createdAt)
            return (
              <div className="text-xs text-muted-foreground">
                {getRelativeTime(date)}
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
            const version = row.original
            const isCurrent =
              currentVersionMap.get(version.repository_id) === version.id
            const isPromoting = promotingId === version.id && promotePending

            if (isCurrent) return null

            return (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => handlePromote(version)}
                disabled={isPromoting}
              >
                {isPromoting ? (
                  "Promoting..."
                ) : (
                  <>
                    <CheckIcon className="mr-1 size-3" />
                    Promote
                  </>
                )}
              </Button>
            )
          },
        },
      ]}
      toolbarProps={defaultToolbarProps}
    />
  )
}

const defaultToolbarProps: DataTableToolbarProps<RegistryVersionRead> = {
  filterProps: {
    placeholder: "Filter versions...",
    column: "version",
  },
}
