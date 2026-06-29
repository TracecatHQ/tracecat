"use client"

import type { Row } from "@tanstack/react-table"
import { CheckIcon, RefreshCwIcon } from "lucide-react"
import { useCallback, useState } from "react"
import type { tracecat__admin__registry__schemas__RegistryVersionRead } from "@/client"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
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
  const [selectedVersions, setSelectedVersions] = useState<
    RegistryVersionRead[]
  >([])
  const [clearSelectionTrigger, setClearSelectionTrigger] = useState(0)
  const { versions, isLoading } = useAdminRegistryVersions()
  const { status } = useAdminRegistryStatus()
  const {
    promoteVersion,
    promotePending,
    backfillArtifacts,
    backfillArtifactsPending,
  } = useAdminRegistrySync()

  const handleSelectionChange = useCallback(
    (rows: Row<RegistryVersionRead>[]) => {
      const nextVersions = rows.map((row) => row.original)
      setSelectedVersions((currentVersions) => {
        if (
          currentVersions.length === nextVersions.length &&
          currentVersions.every(
            (version, index) => version.id === nextVersions[index]?.id
          )
        ) {
          return currentVersions
        }
        return nextVersions
      })
    },
    []
  )

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

  const handleBackfillSelected = async () => {
    if (selectedVersions.length === 0) return

    try {
      const result = await backfillArtifacts({
        version_ids: selectedVersions.map((version) => version.id),
      })
      toast({
        title: "Artifact backfill started",
        description: `Workflow ${result.workflow_id} queued ${result.requested_count} version${result.requested_count === 1 ? "" : "s"}.`,
      })
      setSelectedVersions([])
      setClearSelectionTrigger((value) => value + 1)
    } catch (error) {
      console.error("Failed to start artifact backfill", error)
      toast({
        title: "Failed to start artifact backfill",
        description: "Please try again.",
        variant: "destructive",
      })
    }
  }

  const selectedMissingCount = selectedVersions.filter(
    (version) => !(version.artifacts_ready ?? false)
  ).length

  if (isLoading) {
    return <div className="text-muted-foreground">Loading versions...</div>
  }

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3">
        <div className="text-xs text-muted-foreground">
          {selectedVersions.length > 0
            ? `${selectedVersions.length} selected, ${selectedMissingCount} missing artifacts`
            : "No versions selected"}
        </div>
        <Button
          variant="outline"
          size="sm"
          onClick={handleBackfillSelected}
          disabled={selectedVersions.length === 0 || backfillArtifactsPending}
        >
          <RefreshCwIcon className="mr-1 size-3" />
          {backfillArtifactsPending ? "Starting..." : "Backfill artifacts"}
        </Button>
      </div>
      <DataTable
        data={versions ?? []}
        getRowId={(version) => version.id}
        initialSortingState={[{ id: "created_at", desc: true }]}
        columns={[
          {
            id: "select",
            header: ({ table }) => (
              <Checkbox
                aria-label="Select visible versions"
                checked={
                  table.getIsAllPageRowsSelected()
                    ? true
                    : table.getIsSomePageRowsSelected()
                      ? "indeterminate"
                      : false
                }
                onCheckedChange={(value) =>
                  table.toggleAllPageRowsSelected(!!value)
                }
              />
            ),
            cell: ({ row }) => (
              <Checkbox
                aria-label={`Select version ${row.original.version}`}
                checked={row.getIsSelected()}
                onCheckedChange={(value) => row.toggleSelected(!!value)}
                onClick={(event) => event.stopPropagation()}
              />
            ),
            enableSorting: false,
            enableHiding: false,
          },
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
              return (
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono">{version.version}</span>
                  {(version.is_current ?? false) && (
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
            accessorKey: "artifacts_ready",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Artifacts"
              />
            ),
            cell: ({ row }) => {
              const version = row.original
              return (
                <Badge
                  variant={
                    (version.artifacts_ready ?? false) ? "secondary" : "outline"
                  }
                  className="font-normal"
                >
                  {(version.artifacts_ready ?? false) ? "Ready" : "Missing"}
                </Badge>
              )
            },
            enableSorting: true,
            enableHiding: false,
          },
          {
            accessorKey: "in_use",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Usage"
              />
            ),
            cell: ({ row }) => {
              const version = row.original
              return (
                <div className="flex items-center gap-2">
                  <Badge
                    variant={
                      (version.in_use ?? false) ? "secondary" : "outline"
                    }
                    className="font-normal"
                  >
                    {(version.in_use ?? false) ? "In use" : "Unused"}
                  </Badge>
                  <span className="text-xs text-muted-foreground">
                    {version.workflow_definition_count ?? 0} defs
                  </span>
                </div>
              )
            },
            enableSorting: true,
            enableHiding: false,
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
              const isPromoting = promotingId === version.id && promotePending

              if (version.is_current ?? false) return null

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
        showSelectedRows
        onSelectionChange={handleSelectionChange}
        clearSelectionTrigger={clearSelectionTrigger}
      />
    </div>
  )
}

const defaultToolbarProps: DataTableToolbarProps<RegistryVersionRead> = {
  filterProps: {
    placeholder: "Filter versions...",
    column: "version",
  },
}
