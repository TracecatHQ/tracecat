"use client"

import {
  GitBranchIcon,
  GitPullRequestArrowIcon,
  RefreshCwIcon,
  UploadCloudIcon,
} from "lucide-react"
import * as React from "react"

import type { ResourceRef, SyncOperation, SyncStateStatus } from "@/client"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useToast } from "@/components/ui/use-toast"
import {
  useWorkspaceSyncChangesetActions,
  useWorkspaceSyncChangesets,
  useWorkspaceSyncPendingChanges,
  useWorkspaceSyncStatus,
} from "@/hooks/use-workspace-sync"
import { cn } from "@/lib/utils"
import { useOptionalWorkspaceId } from "@/providers/workspace-id"

interface WorkspaceSyncStagingProps {
  workspaceId?: string
}

const statusLabels: Record<SyncStateStatus, string> = {
  never_synced: "Never synced",
  clean: "Clean",
  local_dirty: "Local dirty",
  remote_ahead: "Remote ahead",
  diverged: "Diverged",
  conflicted: "Conflicted",
  error: "Error",
}

const operationLabels: Record<SyncOperation, string> = {
  create: "Create",
  update: "Update",
  delete: "Delete",
  archive: "Archive",
  disable: "Disable",
}

function statusClass(status: SyncStateStatus | undefined) {
  switch (status) {
    case "clean":
      return "border-emerald-200 bg-emerald-50 text-emerald-700"
    case "local_dirty":
    case "remote_ahead":
      return "border-amber-200 bg-amber-50 text-amber-700"
    case "diverged":
    case "conflicted":
    case "error":
      return "border-rose-200 bg-rose-50 text-rose-700"
    default:
      return "border-muted-foreground/20 text-muted-foreground"
  }
}

function operationClass(operation: SyncOperation) {
  switch (operation) {
    case "create":
      return "border-emerald-200 bg-emerald-50 text-emerald-700"
    case "update":
      return "border-blue-200 bg-blue-50 text-blue-700"
    default:
      return "border-muted-foreground/20 text-muted-foreground"
  }
}

function errorMessage(error: unknown) {
  if (error instanceof Error) {
    return error.message
  }
  return "Request failed"
}

/**
 * Renders local workspace Git changes and exports selected resources as a changeset.
 */
export function WorkspaceSyncStaging({
  workspaceId: workspaceIdProp,
}: WorkspaceSyncStagingProps) {
  const contextWorkspaceId = useOptionalWorkspaceId()
  const workspaceId = workspaceIdProp ?? contextWorkspaceId
  const { toast } = useToast()
  const statusQuery = useWorkspaceSyncStatus(workspaceId)
  const pendingQuery = useWorkspaceSyncPendingChanges(workspaceId)
  const changesetsQuery = useWorkspaceSyncChangesets(workspaceId, { limit: 5 })
  const { createChangeset, exportChangeset } =
    useWorkspaceSyncChangesetActions(workspaceId)

  const changes = React.useMemo(
    () => pendingQuery.data?.changes ?? [],
    [pendingQuery.data?.changes]
  )
  const selectionInitializedRef = React.useRef(false)
  const [selectedSourceIds, setSelectedSourceIds] = React.useState<Set<string>>(
    new Set()
  )
  const [branch, setBranch] = React.useState("")
  const [message, setMessage] = React.useState("Export workspace sync changes")
  const [createPr, setCreatePr] = React.useState(true)

  React.useEffect(() => {
    if (!pendingQuery.data) {
      return
    }
    const sourceIds = new Set(changes.map((change) => change.source_id))
    setSelectedSourceIds((current) => {
      if (sourceIds.size === 0) {
        return sourceIds
      }
      if (!selectionInitializedRef.current) {
        selectionInitializedRef.current = true
        return sourceIds
      }
      return new Set([...current].filter((sourceId) => sourceIds.has(sourceId)))
    })
  }, [changes, pendingQuery.data])

  React.useEffect(() => {
    if (branch || changes.length === 0) {
      return
    }
    setBranch(`sync/${changes[0].source_id}`)
  }, [branch, changes])

  if (!workspaceId) {
    return (
      <div className="rounded-lg border p-4 text-sm text-muted-foreground">
        Workspace context unavailable.
      </div>
    )
  }

  const selectedChanges = changes.filter((change) =>
    selectedSourceIds.has(change.source_id)
  )
  const isBusy = createChangeset.isPending || exportChangeset.isPending
  const canExport =
    selectedChanges.length > 0 && branch.trim().length > 0 && !isBusy

  async function handleExport() {
    if (!workspaceId || !canExport) {
      return
    }

    try {
      const resources: ResourceRef[] = selectedChanges.map((change) => ({
        resource_type: change.resource_type,
        source_id: change.source_id,
        source_path: change.source_path,
        local_id: change.local_id,
      }))
      const title = message.trim() || "Export workspace sync changes"
      const changeset = await createChangeset.mutateAsync({
        title,
        resources,
      })
      const result = await exportChangeset.mutateAsync({
        changesetId: changeset.id,
        requestBody: {
          message: title,
          branch: branch.trim(),
          create_pr: createPr,
        },
      })

      toast({
        title: result.commit.pr_url ? "Pull request ready" : "Changes exported",
        description:
          result.commit.pr_url ?? result.commit.sha ?? result.commit.message,
      })
    } catch (error) {
      toast({
        title: "Export failed",
        description: errorMessage(error),
        variant: "destructive",
      })
    }
  }

  return (
    <div className="space-y-8">
      <section className="rounded-lg border">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3">
          <div>
            <h3 className="text-sm font-medium">Workspace sync</h3>
            <p className="text-xs text-muted-foreground">
              {statusQuery.data?.target_ref
                ? `Tracking ${statusQuery.data.target_ref}`
                : "No tracked ref"}
            </p>
          </div>
          <div className="flex items-center gap-2">
            <Badge
              variant="outline"
              className={cn(statusClass(statusQuery.data?.status))}
            >
              {statusQuery.data?.status
                ? statusLabels[statusQuery.data.status]
                : "Loading"}
            </Badge>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                statusQuery.refetch()
                pendingQuery.refetch()
                changesetsQuery.refetch()
              }}
              disabled={
                statusQuery.isFetching ||
                pendingQuery.isFetching ||
                changesetsQuery.isFetching
              }
            >
              <RefreshCwIcon className="size-3.5" />
              Refresh
            </Button>
          </div>
        </div>

        {statusQuery.isLoading || pendingQuery.isLoading ? (
          <div className="py-10">
            <CenteredSpinner />
          </div>
        ) : statusQuery.error || pendingQuery.error ? (
          <div className="p-4 text-sm text-rose-600">
            {errorMessage(statusQuery.error ?? pendingQuery.error)}
          </div>
        ) : (
          <>
            <div className="grid gap-3 border-b px-4 py-3 text-xs text-muted-foreground sm:grid-cols-4">
              <div>
                <span className="block text-foreground">
                  {statusQuery.data?.pending_change_count ?? changes.length}
                </span>
                Pending
              </div>
              <div>
                <span className="block truncate text-foreground">
                  {statusQuery.data?.base_commit_sha ?? "None"}
                </span>
                Base commit
              </div>
              <div>
                <span className="block truncate text-foreground">
                  {statusQuery.data?.remote_commit_sha ?? "Unavailable"}
                </span>
                Remote commit
              </div>
              <div>
                <span className="block truncate text-foreground">
                  {pendingQuery.data?.local_spec_hash}
                </span>
                Local spec
              </div>
            </div>

            <div className="overflow-x-auto">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-10" />
                    <TableHead>Resource</TableHead>
                    <TableHead>Operation</TableHead>
                    <TableHead>Path</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {changes.length === 0 ? (
                    <TableRow>
                      <TableCell
                        colSpan={4}
                        className="h-20 text-center text-sm text-muted-foreground"
                      >
                        No pending changes
                      </TableCell>
                    </TableRow>
                  ) : (
                    changes.map((change) => (
                      <TableRow key={change.source_id}>
                        <TableCell>
                          <Checkbox
                            checked={selectedSourceIds.has(change.source_id)}
                            disabled={!change.exportable}
                            onCheckedChange={(checked) => {
                              setSelectedSourceIds((current) => {
                                const next = new Set(current)
                                if (checked === true) {
                                  next.add(change.source_id)
                                } else {
                                  next.delete(change.source_id)
                                }
                                return next
                              })
                            }}
                          />
                        </TableCell>
                        <TableCell>
                          <div className="text-sm font-medium">
                            {change.title ?? change.source_id}
                          </div>
                          <div className="text-xs text-muted-foreground">
                            {change.alias ?? change.source_id}
                          </div>
                        </TableCell>
                        <TableCell>
                          <Badge
                            variant="outline"
                            className={cn(operationClass(change.operation))}
                          >
                            {operationLabels[change.operation]}
                          </Badge>
                        </TableCell>
                        <TableCell className="text-xs text-muted-foreground">
                          {change.source_path}
                        </TableCell>
                      </TableRow>
                    ))
                  )}
                </TableBody>
              </Table>
            </div>

            <div className="grid gap-3 border-t p-4 md:grid-cols-[1fr_1fr_auto]">
              <div className="space-y-2">
                <Label htmlFor="sync-message">Message</Label>
                <Input
                  id="sync-message"
                  value={message}
                  onChange={(event) => setMessage(event.target.value)}
                />
              </div>
              <div className="space-y-2">
                <Label htmlFor="sync-branch">Branch</Label>
                <Input
                  id="sync-branch"
                  value={branch}
                  onChange={(event) => setBranch(event.target.value)}
                />
              </div>
              <div className="flex items-end gap-3">
                <label className="flex h-9 items-center gap-2 text-sm">
                  <Checkbox
                    checked={createPr}
                    onCheckedChange={(checked) => setCreatePr(checked === true)}
                  />
                  PR
                </label>
                <Button onClick={handleExport} disabled={!canExport}>
                  <UploadCloudIcon className="size-3.5" />
                  {isBusy ? "Exporting" : "Export"}
                </Button>
              </div>
            </div>
          </>
        )}
      </section>

      <section className="rounded-lg border">
        <div className="flex items-center gap-2 border-b px-4 py-3">
          <GitPullRequestArrowIcon className="size-4 text-muted-foreground" />
          <h3 className="text-sm font-medium">Recent changesets</h3>
        </div>
        <div className="divide-y">
          {(changesetsQuery.data ?? []).length === 0 ? (
            <div className="p-4 text-sm text-muted-foreground">
              No changesets
            </div>
          ) : (
            (changesetsQuery.data ?? []).map((changeset) => (
              <div
                key={changeset.id}
                className="flex flex-wrap items-center justify-between gap-3 px-4 py-3"
              >
                <div className="min-w-0">
                  <div className="truncate text-sm font-medium">
                    {changeset.title}
                  </div>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <GitBranchIcon className="size-3.5" />
                    <span>{changeset.selected_paths.length} files</span>
                    <span>{changeset.status}</span>
                  </div>
                </div>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    setMessage(changeset.title)
                    setSelectedSourceIds(
                      new Set(
                        changeset.selected_resources
                          .map((resource) => resource.source_id)
                          .filter(
                            (sourceId): sourceId is string =>
                              typeof sourceId === "string"
                          )
                      )
                    )
                  }}
                >
                  Select
                </Button>
              </div>
            ))
          )}
        </div>
      </section>
    </div>
  )
}
