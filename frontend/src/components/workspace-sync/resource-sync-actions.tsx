"use client"

import { GitBranchIcon } from "lucide-react"
import { useEffect, useState } from "react"
import type { ResourceRef, SyncResourceType } from "@/client"
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
import { toast } from "@/components/ui/use-toast"
import {
  useWorkspaceSyncBranchTarget,
  WorkspaceSyncBranchSelector,
} from "@/components/workspace-sync/branch-target-selector"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import {
  useRepositoryBranches,
  useWorkspaceSyncExport,
} from "@/hooks/use-workspace-sync"
import { getApiErrorDetail } from "@/lib/errors"
import { useWorkspaceId } from "@/providers/workspace-id"

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
  const {
    branch: exportBranch,
    setBranch: setExportBranch,
    isCreatingBranch,
    selectBranch: selectExportBranch,
    resetBranchCreation,
    hasBranches,
  } = useWorkspaceSyncBranchTarget({
    branches: repoBranches,
    enabled: open,
    newBranchName: `sync/${branchSlug}`,
  })
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
    resetBranchCreation()
    setExportMessage(`Push ${label}`)
  }, [label, open, resetBranchCreation])

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
        create_pr: true,
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
                <WorkspaceSyncBranchSelector
                  id={`sync-branch-${branchSlug}`}
                  branches={repoBranches}
                  branch={exportBranch}
                  isCreatingBranch={isCreatingBranch}
                  branchesIsLoading={branchesIsLoading}
                  hasBranches={hasBranches}
                  branchesError={branchesError}
                  newBranchPlaceholder={`sync/${branchSlug}`}
                  onSelectBranch={selectExportBranch}
                  onBranchChange={setExportBranch}
                  showNoBranchesMessage={Boolean(gitRepoUrl)}
                />
              </div>
            </div>
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
