"use client"

import {
  ArrowRightIcon,
  GitBranchIcon,
  GitPullRequestIcon,
  LayersIcon,
} from "lucide-react"
import { type ReactNode, useEffect, useMemo, useState } from "react"
import type { ResourceRef, SyncResourceType, VcsProvider } from "@/client"
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
  getWorkspaceSyncBaseBranch,
  useWorkspaceSyncBranchTarget,
  WorkspaceSyncBranchSelector,
} from "@/components/workspace-sync/branch-target-selector"
import {
  formatResourceTotal,
  getWorkspaceSyncPreviewResourceTotal,
} from "@/components/workspace-sync/push-resource-manifest"
import {
  getReviewRequestAbbreviation,
  getReviewRequestLabel,
  getWorkspaceSyncPushButtonLabel,
  getWorkspaceSyncPushOutcome,
  getWorkspaceSyncPushResultLabel,
  getWorkspaceSyncPushWarning,
  WorkspaceSyncPushWarning,
} from "@/components/workspace-sync/push-target-policy"
import { PushResourcePreview } from "@/components/workspace-sync/resource-diff-review"
import { useEntitlements } from "@/hooks/use-entitlements"
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
  const canSyncWorkspace = useScopeCheck("workspace_sync:sync")
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const canPushWorkspaceSync =
    canSyncWorkspace === true &&
    !entitlementsLoading &&
    hasEntitlement("git_sync")
  const { workspace } = useWorkspaceDetails()
  const [open, setOpen] = useState(false)
  const [exportMessage, setExportMessage] = useState(`Push ${label}`)
  const { exportWorkspace, exportWorkspaceIsPending } =
    useWorkspaceSyncExport(workspaceId)
  const gitRepoUrl = workspace?.settings?.git_repo_url || undefined
  const provider: VcsProvider = workspace?.settings?.git_provider ?? "github"
  const {
    branches: repoBranches,
    branchesIsLoading,
    branchesError,
  } = useRepositoryBranches(workspaceId, {
    enabled: open && Boolean(workspace?.settings?.git_repo_url),
    gitRepoUrl,
    provider,
    limit: 200,
  })

  const resourceRefs = useMemo<ResourceRef[]>(
    () => resources.map((resourceType) => ({ resource_type: resourceType })),
    [resources]
  )
  const {
    branch: exportBranch,
    setBranch: setExportBranch,
    isCreatingBranch,
    selectBranch: selectExportBranch,
    resetBranchCreation,
    hasBranches,
  } = useWorkspaceSyncBranchTarget({
    branches: repoBranches,
    newBranchPrefix: `sync/${branchSlug}`,
  })

  const repoName = getRepoDisplayName(gitRepoUrl)
  const baseBranch = getWorkspaceSyncBaseBranch(gitRepoUrl, repoBranches)
  const targetBranch = exportBranch.trim()
  const compareRef = isCreatingBranch ? baseBranch : targetBranch || undefined
  const { preview, previewIsLoading, previewError, refetchPreview } =
    useWorkspaceSyncExportPreview(workspaceId, {
      resources: resourceRefs,
      compareRef,
      provider,
      enabled: false,
    })
  const [previewRequested, setPreviewRequested] = useState(false)
  const visiblePreview = previewRequested ? preview : undefined
  const visiblePreviewIsLoading = previewRequested && previewIsLoading
  // The projection pulls in the dependency closure (e.g. secrets a workflow
  // references), so the honest "Source" total spans every type, not just the
  // types the button requested.
  const totalResourceCount =
    getWorkspaceSyncPreviewResourceTotal(visiblePreview)
  const pushOutcome = getWorkspaceSyncPushOutcome({
    mode: "pull-request",
    targetBranch,
    defaultBranch: baseBranch,
    isCreatingBranch,
  })
  const pushWarning = getWorkspaceSyncPushWarning({
    outcome: pushOutcome,
    defaultBranch: baseBranch,
    allowDirectPush: false,
    provider,
  })
  const reviewRequestLabel = getReviewRequestLabel(provider)
  const reviewRequestTitle =
    reviewRequestLabel === "merge request" ? "Merge request" : "Pull request"
  const previewErrorMessage = previewError
    ? (getApiErrorDetail(previewError) ?? "Request failed")
    : undefined
  const exportDisabled =
    !gitRepoUrl ||
    exportWorkspaceIsPending ||
    branchesIsLoading ||
    (!hasBranches && !isCreatingBranch) ||
    pushOutcome.isPullRequestBlocked ||
    targetBranch === "" ||
    exportMessage.trim() === ""

  useEffect(() => {
    if (!open) {
      return
    }
    resetBranchCreation()
    setExportMessage(`Push ${label}`)
    setPreviewRequested(false)
  }, [label, open, resetBranchCreation])

  useEffect(() => {
    setPreviewRequested(false)
  }, [compareRef, provider])

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
        create_pr: pushOutcome.createPr,
        include_schedules: false,
        resources: resourceRefs,
      })
      const prUrl = result.commit.pr_url
      toast({
        title: prUrl ? `${reviewRequestTitle} ready` : "Push complete",
        description: result.commit.message ?? result.commit.sha ?? undefined,
        action: prUrl ? (
          <ToastAction
            altText={`Open ${reviewRequestLabel}`}
            onClick={() => window.open(prUrl, "_blank", "noopener,noreferrer")}
          >
            View {getReviewRequestAbbreviation(provider)}
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

  function handlePreview() {
    if (!compareRef) {
      return
    }
    setPreviewRequested(true)
    void refetchPreview()
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm" className="h-7 gap-1.5 bg-white">
          <GitBranchIcon className="size-3.5" />
          Push
        </Button>
      </DialogTrigger>
      <DialogContent className="flex max-h-[85vh] max-w-2xl flex-col gap-0 overflow-hidden p-0">
        <DialogHeader className="shrink-0 flex-row items-start gap-3 space-y-0 border-b p-6">
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
          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-6">
            <PushFlow
              total={totalResourceCount}
              isLoading={visiblePreviewIsLoading}
              targetBranch={targetBranch}
              defaultBranch={baseBranch}
              outcome={pushOutcome}
              provider={provider}
            />

            <PushResourcePreview
              preview={visiblePreview}
              isLoading={visiblePreviewIsLoading}
              compareRef={compareRef}
              errorMessage={previewRequested ? previewErrorMessage : undefined}
              hasRequestedPreview={previewRequested}
              onRequestPreview={handlePreview}
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
              <WorkspaceSyncPushWarning
                warning={pushWarning}
                blocked={pushOutcome.isPullRequestBlocked}
              />
            </div>
          </div>
        ) : null}

        <DialogFooter className="shrink-0 items-center gap-3 border-t bg-muted/30 px-6 py-4 sm:justify-between">
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
              {pushOutcome.createPr ? (
                <GitPullRequestIcon className="size-4" />
              ) : (
                <GitBranchIcon className="size-4" />
              )}
              {getWorkspaceSyncPushButtonLabel({
                outcome: pushOutcome,
                isCreatingBranch,
                isPending: exportWorkspaceIsPending,
                provider,
              })}
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface PushFlowProps {
  total: number | undefined
  isLoading: boolean
  targetBranch: string
  defaultBranch: string | undefined
  outcome: ReturnType<typeof getWorkspaceSyncPushOutcome>
  provider: VcsProvider
}

/**
 * Visual source -> branch -> pull request strip describing exactly where the
 * selected resources land when pushed. "Source" reports the full projection
 * total; the manifest below breaks it down by resource type.
 */
function PushFlow({
  total,
  isLoading,
  targetBranch,
  defaultBranch,
  outcome,
  provider,
}: PushFlowProps) {
  return (
    <div className="flex items-stretch gap-2 rounded-lg border bg-muted/30 p-3">
      <FlowNode
        icon={<LayersIcon className="size-3.5" />}
        title="Source"
        value={
          isLoading && total === undefined ? (
            <Skeleton className="h-4 w-16 rounded-sm" />
          ) : (
            formatResourceTotal(total)
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
        icon={
          outcome.createPr ? (
            <GitPullRequestIcon className="size-3.5" />
          ) : (
            <GitBranchIcon className="size-3.5" />
          )
        }
        title="Result"
        value={getWorkspaceSyncPushResultLabel({
          outcome,
          defaultBranch,
          provider,
        })}
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
  return `Commit all ${label} in this workspace to the selected Git branch.`
}
