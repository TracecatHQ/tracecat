"use client"

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import {
  ArrowLeftIcon,
  CheckCircleIcon,
  DiffIcon,
  HistoryIcon,
  TrashIcon,
} from "lucide-react"
import { useState } from "react"
import type {
  RegistryRepositoryReadMinimal,
  tracecat__registry__repositories__schemas__RegistryVersionRead,
  VersionDiff,
} from "@/client"
import {
  registryRepositoriesCompareRegistryVersions,
  registryRepositoriesDeleteRegistryVersion,
  registryRepositoriesGetPreviousRegistryVersion,
  registryRepositoriesListRepositoryVersions,
  registryRepositoriesPromoteRegistryVersion,
} from "@/client/services.gen"
import { Spinner } from "@/components/loading/spinner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { getRelativeTime } from "@/lib/event-history"

interface RepositoryVersionsDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedRepo: RegistryRepositoryReadMinimal | null
}

type DialogView = "versions" | "diff"

export function RepositoryVersionsDialog({
  open,
  onOpenChange,
  selectedRepo,
}: RepositoryVersionsDialogProps) {
  const queryClient = useQueryClient()
  const [view, setView] = useState<DialogView>("versions")
  const [compareBaseId, setCompareBaseId] = useState<string | null>(null)
  const [compareToId, setCompareToId] = useState<string | null>(null)

  // Reset state when dialog closes
  function handleOpenChange(open: boolean) {
    if (!open) {
      setView("versions")
      setCompareBaseId(null)
      setCompareToId(null)
    }
    onOpenChange(open)
  }

  // Fetch versions
  const { data: versions, isLoading: versionsLoading } = useQuery({
    queryKey: ["registry_versions", selectedRepo?.id],
    queryFn: async () => {
      if (!selectedRepo?.id) return []
      return await registryRepositoriesListRepositoryVersions({
        repositoryId: selectedRepo.id,
      })
    },
    enabled: open && !!selectedRepo?.id,
  })

  // Fetch previous version for quick rollback
  const { data: previousVersion } = useQuery({
    queryKey: [
      "registry_previous_version",
      selectedRepo?.id,
      selectedRepo?.current_version_id,
    ],
    queryFn: async () => {
      if (!selectedRepo?.id || !selectedRepo?.current_version_id) return null
      return await registryRepositoriesGetPreviousRegistryVersion({
        repositoryId: selectedRepo.id,
        versionId: selectedRepo.current_version_id,
      })
    },
    enabled: open && !!selectedRepo?.id && !!selectedRepo?.current_version_id,
  })

  // Fetch diff when comparing
  const { data: diff, isLoading: diffLoading } = useQuery({
    queryKey: ["registry_version_diff", compareBaseId, compareToId],
    queryFn: async () => {
      if (!selectedRepo?.id || !compareBaseId || !compareToId) return null
      return await registryRepositoriesCompareRegistryVersions({
        repositoryId: selectedRepo.id,
        versionId: compareBaseId,
        compareTo: compareToId,
      })
    },
    enabled:
      view === "diff" && !!selectedRepo?.id && !!compareBaseId && !!compareToId,
  })

  // Promote version mutation
  const { mutateAsync: promoteVersion, isPending: promotePending } =
    useMutation({
      mutationFn: async ({
        versionId,
      }: {
        versionId: string
        versionName: string
      }) => {
        if (!selectedRepo?.id) throw new Error("No repository selected")
        return await registryRepositoriesPromoteRegistryVersion({
          repositoryId: selectedRepo.id,
          versionId,
        })
      },
      onSuccess: (_, { versionName }) => {
        queryClient.invalidateQueries({ queryKey: ["registry_repositories"] })
        queryClient.invalidateQueries({
          queryKey: ["registry_versions", selectedRepo?.id],
        })
        queryClient.invalidateQueries({
          queryKey: ["registry_previous_version", selectedRepo?.id],
        })
        toast({
          title: "Version promoted",
          description: `Version ${versionName} is now active.`,
        })
      },
      onError: (error) => {
        console.error("Failed to promote version", error)
        toast({
          title: "Failed to promote version",
          description: "An error occurred while promoting the version.",
          variant: "destructive",
        })
      },
    })

  // Delete version mutation
  const { mutateAsync: deleteVersion, isPending: deletePending } = useMutation({
    mutationFn: async (versionId: string) => {
      if (!selectedRepo?.id) throw new Error("No repository selected")
      return await registryRepositoriesDeleteRegistryVersion({
        repositoryId: selectedRepo.id,
        versionId,
      })
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["registry_repositories"] })
      queryClient.invalidateQueries({
        queryKey: ["registry_versions", selectedRepo?.id],
      })
      queryClient.invalidateQueries({
        queryKey: ["registry_previous_version", selectedRepo?.id],
      })
      toast({
        title: "Version deleted",
        description: "The version has been deleted.",
      })
    },
    onError: (error: unknown) => {
      console.error("Failed to delete version", error)
      const apiError = error as { body?: { detail?: string } }
      toast({
        title: "Failed to delete version",
        description:
          apiError.body?.detail ||
          "An error occurred while deleting the version.",
        variant: "destructive",
      })
    },
  })

  function handleCompare(
    baseVersion: tracecat__registry__repositories__schemas__RegistryVersionRead
  ) {
    setCompareBaseId(baseVersion.id)
    // Default to comparing with current version if different, otherwise first other version
    if (selectedRepo?.current_version_id !== baseVersion.id) {
      setCompareToId(selectedRepo?.current_version_id ?? null)
    } else if (versions && versions.length > 1) {
      const otherVersion = versions.find((v) => v.id !== baseVersion.id)
      setCompareToId(otherVersion?.id ?? null)
    }
    setView("diff")
  }

  function handleQuickRollback() {
    if (previousVersion) {
      const prev =
        previousVersion as tracecat__registry__repositories__schemas__RegistryVersionRead
      promoteVersion({ versionId: prev.id, versionName: prev.version })
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            {view === "diff" && (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setView("versions")}
              >
                <ArrowLeftIcon className="size-4" />
              </Button>
            )}
            <HistoryIcon className="size-5" />
            {view === "versions" ? "Manage versions" : "Compare versions"}
          </DialogTitle>
          <DialogDescription>
            {view === "versions"
              ? `Manage versions for ${selectedRepo?.origin ?? "repository"}`
              : "View changes between two versions"}
          </DialogDescription>
        </DialogHeader>

        {view === "versions" && (
          <VersionsView
            versions={versions ?? []}
            versionsLoading={versionsLoading}
            currentVersionId={selectedRepo?.current_version_id ?? null}
            previousVersion={
              previousVersion as tracecat__registry__repositories__schemas__RegistryVersionRead | null
            }
            onPromote={promoteVersion}
            onDelete={deleteVersion}
            onCompare={handleCompare}
            onQuickRollback={handleQuickRollback}
            promotePending={promotePending}
            deletePending={deletePending}
          />
        )}

        {view === "diff" && (
          <DiffView
            diff={diff ?? null}
            diffLoading={diffLoading}
            versions={versions ?? []}
            compareBaseId={compareBaseId}
            compareToId={compareToId}
            onBaseChange={setCompareBaseId}
            onCompareChange={setCompareToId}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

interface VersionsViewProps {
  versions: tracecat__registry__repositories__schemas__RegistryVersionRead[]
  versionsLoading: boolean
  currentVersionId: string | null
  previousVersion: tracecat__registry__repositories__schemas__RegistryVersionRead | null
  onPromote: (params: {
    versionId: string
    versionName: string
  }) => Promise<unknown>
  onDelete: (versionId: string) => Promise<unknown>
  onCompare: (
    version: tracecat__registry__repositories__schemas__RegistryVersionRead
  ) => void
  onQuickRollback: () => void
  promotePending: boolean
  deletePending: boolean
}

function VersionsView({
  versions,
  versionsLoading,
  currentVersionId,
  previousVersion,
  onPromote,
  onDelete,
  onCompare,
  onQuickRollback,
  promotePending,
  deletePending,
}: VersionsViewProps) {
  if (versionsLoading) {
    return (
      <div className="flex items-center justify-center py-8">
        <Spinner className="size-6" />
      </div>
    )
  }

  if (!versions.length) {
    return (
      <div className="py-8 text-center text-muted-foreground">
        No versions found
      </div>
    )
  }

  return (
    <div className="space-y-4">
      {previousVersion && (
        <div className="flex items-center justify-between rounded-md border bg-muted/50 p-3">
          <div className="text-sm">
            <span className="text-muted-foreground">Quick rollback to: </span>
            <span className="font-mono">{previousVersion.version}</span>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={onQuickRollback}
            disabled={promotePending}
          >
            <HistoryIcon className="mr-2 size-4" />
            Rollback
          </Button>
        </div>
      )}

      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Version</TableHead>
            <TableHead>Commit</TableHead>
            <TableHead>Created</TableHead>
            <TableHead>Status</TableHead>
            <TableHead className="text-right">Actions</TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {versions.map((version) => {
            const isCurrent = version.id === currentVersionId
            return (
              <TableRow key={version.id}>
                <TableCell className="font-mono text-sm">
                  {version.version}
                </TableCell>
                <TableCell>
                  {version.commit_sha ? (
                    <Badge variant="secondary" className="font-mono text-xs">
                      {version.commit_sha.substring(0, 7)}
                    </Badge>
                  ) : (
                    <span className="text-muted-foreground">-</span>
                  )}
                </TableCell>
                <TableCell className="text-sm text-muted-foreground">
                  {getRelativeTime(new Date(version.created_at))}
                </TableCell>
                <TableCell>
                  {isCurrent && (
                    <Badge variant="default" className="gap-1">
                      <CheckCircleIcon className="size-3" />
                      Current
                    </Badge>
                  )}
                </TableCell>
                <TableCell className="text-right">
                  <TooltipProvider>
                    <div className="flex items-center justify-end gap-1">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => onCompare(version)}
                          >
                            <DiffIcon className="size-4" />
                          </Button>
                        </TooltipTrigger>
                        <TooltipContent>
                          <p>Compare with another version</p>
                        </TooltipContent>
                      </Tooltip>
                      {!isCurrent && (
                        <>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() =>
                                  onPromote({
                                    versionId: version.id,
                                    versionName: version.version,
                                  })
                                }
                                disabled={promotePending}
                              >
                                <CheckCircleIcon className="size-4" />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>
                              <p>Promote this version</p>
                            </TooltipContent>
                          </Tooltip>
                          <Tooltip>
                            <TooltipTrigger asChild>
                              <Button
                                variant="ghost"
                                size="sm"
                                onClick={() => onDelete(version.id)}
                                disabled={deletePending}
                                className="text-destructive hover:text-destructive"
                              >
                                <TrashIcon className="size-4" />
                              </Button>
                            </TooltipTrigger>
                            <TooltipContent>
                              <p>Delete this version</p>
                            </TooltipContent>
                          </Tooltip>
                        </>
                      )}
                    </div>
                  </TooltipProvider>
                </TableCell>
              </TableRow>
            )
          })}
        </TableBody>
      </Table>
    </div>
  )
}

interface DiffViewProps {
  diff: VersionDiff | null
  diffLoading: boolean
  versions: tracecat__registry__repositories__schemas__RegistryVersionRead[]
  compareBaseId: string | null
  compareToId: string | null
  onBaseChange: (id: string | null) => void
  onCompareChange: (id: string | null) => void
}

function DiffView({
  diff,
  diffLoading,
  versions,
  compareBaseId,
  compareToId,
  onBaseChange,
  onCompareChange,
}: DiffViewProps) {
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-4">
        <div className="flex-1">
          <label className="mb-1 block text-sm font-medium">Base version</label>
          <Select
            value={compareBaseId ?? undefined}
            onValueChange={onBaseChange}
          >
            <SelectTrigger>
              <SelectValue placeholder="Select version" />
            </SelectTrigger>
            <SelectContent>
              {versions.map((v) => (
                <SelectItem key={v.id} value={v.id}>
                  {v.version}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="flex-1">
          <label className="mb-1 block text-sm font-medium">
            Compare to version
          </label>
          <Select
            value={compareToId ?? undefined}
            onValueChange={onCompareChange}
          >
            <SelectTrigger>
              <SelectValue placeholder="Select version" />
            </SelectTrigger>
            <SelectContent>
              {versions.map((v) => (
                <SelectItem key={v.id} value={v.id}>
                  {v.version}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {diffLoading && (
        <div className="flex items-center justify-center py-8">
          <Spinner className="size-6" />
        </div>
      )}

      {diff && !diffLoading && (
        <div className="space-y-4">
          <div className="flex items-center gap-4 text-sm">
            <Badge variant="secondary" className="gap-1">
              <span className="text-green-600">
                +{diff.actions_added?.length ?? 0}
              </span>
              <span>added</span>
            </Badge>
            <Badge variant="secondary" className="gap-1">
              <span className="text-red-600">
                -{diff.actions_removed?.length ?? 0}
              </span>
              <span>removed</span>
            </Badge>
            <Badge variant="secondary" className="gap-1">
              <span className="text-amber-600">
                ~{diff.actions_modified?.length ?? 0}
              </span>
              <span>modified</span>
            </Badge>
          </div>

          {diff.total_changes === 0 && (
            <div className="py-4 text-center text-muted-foreground">
              No changes between these versions
            </div>
          )}

          {(diff.actions_added?.length ?? 0) > 0 && (
            <div>
              <h4 className="mb-2 font-medium text-green-600">Added actions</h4>
              <ul className="space-y-1 font-mono text-sm">
                {diff.actions_added?.map((action) => (
                  <li key={action} className="text-muted-foreground">
                    + {action}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(diff.actions_removed?.length ?? 0) > 0 && (
            <div>
              <h4 className="mb-2 font-medium text-red-600">Removed actions</h4>
              <ul className="space-y-1 font-mono text-sm">
                {diff.actions_removed?.map((action) => (
                  <li key={action} className="text-muted-foreground">
                    - {action}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {(diff.actions_modified?.length ?? 0) > 0 && (
            <div>
              <h4 className="mb-2 font-medium text-amber-600">
                Modified actions
              </h4>
              <ul className="space-y-2 font-mono text-sm">
                {diff.actions_modified?.map((change) => (
                  <li
                    key={change.action_name}
                    className="text-muted-foreground"
                  >
                    <div>~ {change.action_name}</div>
                    {change.description_changed && (
                      <div className="ml-4 text-xs">Description changed</div>
                    )}
                    {change.interface_changes?.map((ic, idx) => (
                      <div key={idx} className="ml-4 text-xs">
                        {ic.field} {ic.change_type}
                      </div>
                    ))}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
