"use client"

import {
  ArrowRightIcon,
  GitBranchIcon,
  GitPullRequestIcon,
  LayersIcon,
} from "lucide-react"
import { type ReactNode, useEffect, useMemo, useState } from "react"
import type { ResourceRef, SyncResourceType } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { ToastAction } from "@/components/ui/toast"
import { toast } from "@/components/ui/use-toast"
import {
  useWorkspaceSyncBranchTarget,
  WorkspaceSyncBranchSelector,
} from "@/components/workspace-sync/branch-target-selector"
import { useWorkspaceDetails } from "@/hooks/use-workspace"
import {
  useRepositoryBranches,
  useWorkspaceSyncExport,
  useWorkspaceSyncExportPreview,
} from "@/hooks/use-workspace-sync"
import { getApiErrorDetail } from "@/lib/errors"
import { getRepoDisplayName } from "@/lib/git"
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
  const canPushWorkspaceSync =
    useScopeCheck(undefined, ["workflow:update", "workflow:sync"], {
      all: true,
    }) === true
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
  const resourceRefs = useMemo<ResourceRef[]>(
    () => resources.map((resourceType) => ({ resource_type: resourceType })),
    [resources]
  )
  const { resourceCount, previewIsLoading } = useWorkspaceSyncExportPreview(
    workspaceId,
    {
      resources: resourceRefs,
      enabled: open && Boolean(gitRepoUrl),
    }
  )
  const {
    branch: exportBranch,
    setBranch: setExportBranch,
    isCreatingBranch,
    selectBranch: selectExportBranch,
    resetBranchCreation,
    defaultBranch,
    hasBranches,
  } = useWorkspaceSyncBranchTarget({
    branches: repoBranches,
    enabled: open,
    newBranchName: `sync/${branchSlug}`,
  })

  const repoName = getRepoDisplayName(gitRepoUrl)
  const targetBranch = exportBranch.trim()
  const targetIsDefault =
    !isCreatingBranch &&
    Boolean(defaultBranch) &&
    targetBranch === defaultBranch
  const exportDisabled =
    !gitRepoUrl ||
    exportWorkspaceIsPending ||
    branchesIsLoading ||
    (!hasBranches && !isCreatingBranch) ||
    targetIsDefault ||
    targetBranch === "" ||
    exportMessage.trim() === ""

  useEffect(() => {
    if (!open) {
      return
    }
    resetBranchCreation()
    setExportMessage(`Push ${label}`)
  }, [label, open, resetBranchCreation])

  if (!canPushWorkspaceSync) {
    return null
  }

  async function handleExport() {
    if (!gitRepoUrl) {
      return
    }

    try {
      const result = await exportWorkspace({
        message: exportMessage,
        branch: targetBranch,
        create_pr: true,
        include_schedules: false,
        provider: "github",
        resources: resourceRefs,
      })
      const prUrl = result.commit.pr_url
      toast({
        title: prUrl ? "Pull request ready" : "Push complete",
        description: result.commit.message ?? result.commit.sha ?? undefined,
        action: prUrl ? (
          <ToastAction
            altText="Open pull request"
            onClick={() => window.open(prUrl, "_blank", "noopener,noreferrer")}
          >
            View PR
          </ToastAction>
        ) : undefined,
      })
      setOpen(false)
    } catch (error) {
      toast({
        title: "Push failed",
        description: getApiErrorDetail(error) ?? "Request failed",
        variant: "destructive",
      })
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="h-7 gap-1.5 bg-white">
          <GitBranchIcon className="size-3.5" />
          Push
        </Button>
      </DialogTrigger>
      <DialogContent className="max-w-xl gap-0 overflow-hidden p-0">
        <DialogHeader className="flex-row items-start gap-3 space-y-0 border-b p-6">
          <div className="flex size-9 shrink-0 items-center justify-center rounded-lg border border-primary/20 bg-primary/10 text-primary">
            <GitBranchIcon className="size-[18px]" />
          </div>
          <div className="min-w-0 space-y-1">
            <DialogTitle className="text-base">Push {label}</DialogTitle>
            <DialogDescription>
              {describePush({ gitRepoUrl, label })}
            </DialogDescription>
          </div>
        </DialogHeader>

        {gitRepoUrl ? (
          <div className="space-y-5 p-6">
            <PushFlow
              label={label}
              resourceCount={resourceCount}
              resourceCountIsLoading={previewIsLoading}
              targetBranch={targetBranch}
              defaultBranch={defaultBranch}
            />

            <div className="space-y-2">
              <label
                className="text-sm font-medium"
                htmlFor={`sync-message-${branchSlug}`}
              >
                Commit message
              </label>
              <Textarea
                id={`sync-message-${branchSlug}`}
                className="min-h-[64px] resize-none text-sm"
                value={exportMessage}
                onChange={(event) => setExportMessage(event.target.value)}
              />
            </div>

            <div className="space-y-2">
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
              {targetIsDefault ? (
                <p className="text-[11px] text-destructive">
                  Select or create a non-default branch to open a pull request.
                </p>
              ) : null}
            </div>
          </div>
        ) : null}

        <DialogFooter className="items-center gap-3 border-t bg-muted/30 px-6 py-4 sm:justify-between">
          <span className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
            {repoName ? (
              <>
                <GitBranchIcon className="size-3.5 shrink-0" />
                <span className="truncate">{repoName}</span>
              </>
            ) : null}
          </span>
          <div className="flex items-center gap-2">
            <DialogClose asChild>
              <Button type="button" size="sm" variant="outline">
                Cancel
              </Button>
            </DialogClose>
            <Button
              type="button"
              size="sm"
              onClick={handleExport}
              disabled={exportDisabled}
              className="gap-1.5"
            >
              <GitPullRequestIcon className="size-4" />
              {buildPushButtonLabel({
                isPending: exportWorkspaceIsPending,
              })}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface PushFlowProps {
  label: string
  resourceCount: number | undefined
  resourceCountIsLoading: boolean
  targetBranch: string
  defaultBranch: string | undefined
}

/**
 * Visual source -> branch -> pull request strip describing exactly where the
 * selected resources land when pushed.
 */
function PushFlow({
  label,
  resourceCount,
  resourceCountIsLoading,
  targetBranch,
  defaultBranch,
}: PushFlowProps) {
  return (
    <div className="flex items-stretch gap-2 rounded-lg border bg-muted/30 p-3">
      <FlowNode
        icon={<LayersIcon className="size-3.5" />}
        title="Source"
        value={
          resourceCountIsLoading && resourceCount === undefined ? (
            <Skeleton className="h-4 w-16 rounded-sm" />
          ) : (
            formatResourceCount(resourceCount, label)
          )
        }
      />
      <FlowArrow />
      <FlowNode
        icon={<GitBranchIcon className="size-3.5" />}
        title="Branch"
        value={
          <span className="flex items-center gap-1.5">
            <span className="truncate">{targetBranch || "—"}</span>
            {targetBranch && targetBranch === defaultBranch ? (
              <Badge
                variant="secondary"
                className="h-4 shrink-0 rounded-sm px-1 text-[10px] font-normal"
              >
                default
              </Badge>
            ) : null}
          </span>
        }
      />
      <FlowArrow />
      <FlowNode
        icon={<GitPullRequestIcon className="size-3.5" />}
        title="Pull request"
        value={defaultBranch ?? "default branch"}
      />
    </div>
  )
}

interface FlowNodeProps {
  icon: ReactNode
  title: string
  value: ReactNode
}

function FlowNode({ icon, title, value }: FlowNodeProps) {
  return (
    <div className="flex min-w-0 flex-1 flex-col gap-1">
      <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </span>
      <span className="flex min-w-0 items-center gap-1.5 text-sm font-medium">
        <span className="shrink-0 text-muted-foreground">{icon}</span>
        <span className="truncate">{value}</span>
      </span>
    </div>
  )
}

function FlowArrow() {
  return (
    <div className="flex shrink-0 items-center self-center text-muted-foreground/60">
      <ArrowRightIcon className="size-4" />
    </div>
  )
}

interface DescribePushOptions {
  gitRepoUrl: string | undefined
  label: string
}

/**
 * Short header sentence; the flow strip carries the branch and PR specifics.
 */
function describePush({ gitRepoUrl, label }: DescribePushOptions): string {
  if (!gitRepoUrl) {
    return "Configure a Git repository in workspace settings first."
  }
  return `Commit all ${label} in this workspace, then open a pull request for review.`
}

interface BuildPushButtonLabelOptions {
  isPending: boolean
}

function buildPushButtonLabel({
  isPending,
}: BuildPushButtonLabelOptions): string {
  if (isPending) {
    return "Pushing..."
  }
  return "Push & open PR"
}

/**
 * Renders the resource count with a singular/plural-aware label, e.g.
 * "12 agents" or "1 agent". Falls back to the label when no count is known.
 */
function formatResourceCount(count: number | undefined, label: string): string {
  if (count === undefined) {
    return label
  }
  const singular = count === 1 ? label.replace(/s$/, "") : label
  return `${count} ${singular}`
}
