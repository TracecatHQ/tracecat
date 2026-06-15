"use client"

import { GitBranchIcon } from "lucide-react"
import { useEffect, useState } from "react"
import type { ResourceRef, SyncResourceType } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { toast } from "@/components/ui/use-toast"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import {
  useRepositoryBranches,
  useWorkspaceSyncExport,
} from "@/hooks/use-workspace-sync"
import { getApiErrorDetail } from "@/lib/errors"
import { useWorkspaceId } from "@/providers/workspace-id"

const CREATE_NEW_BRANCH_VALUE = "__create_new_branch__"

interface WorkspaceResourceSyncActionsProps {
  label: string
  branchSlug: string
  resources: SyncResourceType[]
}

export function WorkspaceResourceSyncActions({
  label,
  branchSlug,
  resources,
}: WorkspaceResourceSyncActionsProps) {
  const workspaceId = useWorkspaceId()
  const { workspace } = useWorkspaceDetails()
  const [open, setOpen] = useState(false)
  const [exportMessage, setExportMessage] = useState(`Push ${label}`)
  const [exportBranch, setExportBranch] = useState("")
  const [createPr, setCreatePr] = useState(false)
  const [isCreatingBranch, setIsCreatingBranch] = useState(false)
  const { exportWorkspace, exportWorkspaceIsPending } =
    useWorkspaceSyncExport(workspaceId)
  const {
    branches: repoBranches,
    branchesIsLoading,
    branchesError,
  } = useRepositoryBranches(workspaceId, {
    enabled: open && Boolean(workspace?.settings?.git_repo_url),
    limit: 200,
  })

  const gitRepoUrl = workspace?.settings?.git_repo_url || undefined
  const hasBranches = (repoBranches?.length ?? 0) > 0
  const selectedBranchInfo = repoBranches?.find(
    (branch) => branch.name === exportBranch
  )
  const isDefaultBranchSelected = selectedBranchInfo?.is_default ?? false
  const exportDisabled =
    !gitRepoUrl ||
    exportWorkspaceIsPending ||
    branchesIsLoading ||
    (!hasBranches && !isCreatingBranch) ||
    exportBranch.trim() === "" ||
    exportMessage.trim() === ""

  useEffect(() => {
    if (!open) {
      return
    }
    setIsCreatingBranch(false)
    setExportMessage(`Push ${label}`)
    setCreatePr(false)
  }, [label, open])

  useEffect(() => {
    if (
      !open ||
      !repoBranches ||
      repoBranches.length === 0 ||
      isCreatingBranch
    ) {
      return
    }

    const branchNames = new Set(repoBranches.map((branch) => branch.name))
    if (exportBranch && branchNames.has(exportBranch)) {
      return
    }

    const defaultBranch =
      repoBranches.find((branch) => branch.is_default)?.name ??
      repoBranches[0]?.name

    if (defaultBranch) {
      setExportBranch(defaultBranch)
    }
  }, [exportBranch, isCreatingBranch, open, repoBranches])

  async function handleExport() {
    if (!gitRepoUrl) {
      return
    }

    const resourceRefs: ResourceRef[] = resources.map((resourceType) => ({
      resource_type: resourceType,
    }))

    try {
      const result = await exportWorkspace({
        message: exportMessage,
        branch: exportBranch,
        create_pr: createPr,
        include_schedules: false,
        provider: "github",
        resources: resourceRefs,
      })
      toast({
        title: result.commit.pr_url ? "Pull request ready" : "Push complete",
        description:
          result.commit.pr_url ?? result.commit.sha ?? result.commit.message,
      })
    } catch (error) {
      toast({
        title: "Push failed",
        description: getApiErrorDetail(error) ?? "Request failed",
        variant: "destructive",
      })
    }
  }

  return (
    <>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogTrigger asChild>
          <Button variant="outline" size="sm" className="h-7 gap-1.5 bg-white">
            <GitBranchIcon className="size-3.5" />
            Push
          </Button>
        </DialogTrigger>
        <DialogContent className="max-w-xl">
          <DialogHeader>
            <DialogTitle>Push {label} to Git</DialogTitle>
            <DialogDescription>
              {gitRepoUrl
                ? "Push this resource type to a repository branch."
                : "Configure a Git repository in workspace settings first."}
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4">
            <div className="grid min-w-0 grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="min-w-0 space-y-2">
                <label
                  className="text-sm font-medium"
                  htmlFor={`sync-message-${branchSlug}`}
                >
                  Message
                </label>
                <Input
                  id={`sync-message-${branchSlug}`}
                  value={exportMessage}
                  onChange={(event) => setExportMessage(event.target.value)}
                />
              </div>
              <div className="min-w-0 space-y-2">
                <label
                  className="text-sm font-medium"
                  htmlFor={`sync-branch-${branchSlug}`}
                >
                  Target branch
                </label>
                <Select
                  value={
                    isCreatingBranch ||
                    !repoBranches?.some(
                      (branch) => branch.name === exportBranch
                    )
                      ? CREATE_NEW_BRANCH_VALUE
                      : exportBranch
                  }
                  onValueChange={(value) => {
                    if (value === CREATE_NEW_BRANCH_VALUE) {
                      setIsCreatingBranch(true)
                      setExportBranch(`sync/${branchSlug}`)
                      return
                    }
                    setIsCreatingBranch(false)
                    setExportBranch(value)
                  }}
                  disabled={branchesIsLoading || !hasBranches}
                >
                  <SelectTrigger
                    id={`sync-branch-${branchSlug}`}
                    className="min-w-0"
                  >
                    {branchesIsLoading ? (
                      <Skeleton className="h-4 w-full rounded-sm" />
                    ) : (
                      <SelectValue placeholder="Select branch" />
                    )}
                  </SelectTrigger>
                  <SelectContent>
                    {hasBranches ? (
                      <>
                        <SelectItem value={CREATE_NEW_BRANCH_VALUE}>
                          Create new branch...
                        </SelectItem>
                        <SelectSeparator />
                        {(repoBranches ?? []).map((branch) => (
                          <SelectItem key={branch.name} value={branch.name}>
                            <div className="flex items-center gap-2">
                              <span>{branch.name}</span>
                              {branch.is_default && (
                                <Badge
                                  variant="secondary"
                                  className="h-4 rounded-sm px-1 text-[10px] font-normal"
                                >
                                  default
                                </Badge>
                              )}
                            </div>
                          </SelectItem>
                        ))}
                      </>
                    ) : (
                      <SelectItem value="__no_branches" disabled>
                        No branches found
                      </SelectItem>
                    )}
                  </SelectContent>
                </Select>
                {isCreatingBranch && (
                  <div className="space-y-1">
                    <Input
                      value={exportBranch}
                      onChange={(event) => setExportBranch(event.target.value)}
                      placeholder={`sync/${branchSlug}`}
                    />
                    <p className="text-[11px] text-muted-foreground">
                      The branch will be created from the repository default
                      branch.
                    </p>
                  </div>
                )}
                {!branchesIsLoading && !hasBranches && gitRepoUrl && (
                  <p className="text-[11px] text-muted-foreground">
                    No branches available from the configured repository.
                  </p>
                )}
                {branchesError && (
                  <p className="text-[11px] text-destructive">
                    Failed to load repository branches.
                  </p>
                )}
              </div>
            </div>

            <div className="flex items-center justify-between rounded-md border px-3 py-2">
              <div className="space-y-0.5">
                <p className="text-sm font-medium">Create pull request</p>
                <p className="text-[11px] text-muted-foreground">
                  Reuse an open PR for this branch when available.
                </p>
              </div>
              <Switch
                checked={createPr}
                onCheckedChange={setCreatePr}
                size="sm"
              />
            </div>

            {isDefaultBranchSelected && !createPr && (
              <p className="text-[11px] text-amber-700">
                Pushing to the default branch will create a direct commit.
              </p>
            )}
          </div>

          <DialogFooter className="items-center gap-3 sm:justify-between">
            <Button
              type="button"
              size="sm"
              onClick={handleExport}
              disabled={exportDisabled}
              className="gap-1.5"
            >
              <GitBranchIcon className="size-4" />
              {exportWorkspaceIsPending ? "Pushing..." : "Push changes"}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  )
}
