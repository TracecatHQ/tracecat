"use client"

import {
  ArrowUpIcon,
  ChevronDownIcon,
  ExternalLinkIcon,
  GitPullRequestIcon,
  Loader2Icon,
} from "lucide-react"
import { useEffect, useState } from "react"
import type { GitBranchInfo, PullResourceDiff, VcsProvider } from "@/client"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { ToastAction } from "@/components/ui/toast"
import { toast } from "@/components/ui/use-toast"
import {
  useWorkspaceSyncBranchTarget,
  writeLastPushBranch,
} from "@/components/workspace-sync/branch-target-selector"
import { ExportErrorMessage } from "@/components/workspace-sync/export-error"
import { GitSyncActionBar } from "@/components/workspace-sync/git-sync-action-bar"
import { GitSyncBranchPicker } from "@/components/workspace-sync/git-sync-branch-picker"
import {
  formatChangeCounts,
  GitSyncChangeList,
  GitSyncNotice,
  GitSyncPreviewButton,
  GitSyncPreviewEmpty,
} from "@/components/workspace-sync/git-sync-change-list"
import {
  getReviewRequestAbbreviation,
  getReviewRequestLabel,
  getWorkspaceSyncPushOutcome,
  type WorkspaceSyncPushMode,
} from "@/components/workspace-sync/push-target-policy"
import { WorkspacePushExplanation } from "@/components/workspace-sync/workspace-push-explanation"
import { SyncOperationWaitCancelled } from "@/hooks/use-sync-operation"
import {
  useWorkspaceSyncExport,
  useWorkspaceSyncExportPreview,
} from "@/hooks/use-workspace-sync"
import { useQueryClient } from "@/lib/query"

interface GitSyncPushTabProps {
  workspaceId: string
  persistedGitUrl: string
  provider: VcsProvider
  repoBranches: GitBranchInfo[] | undefined
  baseBranch: string | undefined
  branchesIsLoading: boolean
  branchesError: unknown
  /** Changes when a pull lands, since the preview no longer matches. */
  syncRevision: number
}

/**
 * Push composer: the workspace goes into a branch, optionally opening a pull
 * request. The preview lists what is included so it can be skimmed.
 */
export function GitSyncPushTab({
  workspaceId,
  persistedGitUrl,
  provider,
  repoBranches,
  baseBranch,
  branchesIsLoading,
  branchesError,
  syncRevision,
}: GitSyncPushTabProps) {
  const { exportWorkspace, exportWorkspaceIsPending } =
    useWorkspaceSyncExport(workspaceId)
  const queryClient = useQueryClient()

  const [message, setMessage] = useState("Export workspace config")
  const [mode, setMode] = useState<WorkspaceSyncPushMode>("pull-request")
  const [previewRequested, setPreviewRequested] = useState(false)

  // Scoped to the repository so a reconnect never restores another repo's branch.
  const rememberScope = `${workspaceId}:${persistedGitUrl}`
  const { branch, isCreatingBranch, selectBranch, createBranch } =
    useWorkspaceSyncBranchTarget({
      branches: repoBranches,
      newBranchPrefix: "sync/workspace",
      rememberScope,
    })

  const targetBranch = branch.trim()
  // A new branch does not exist yet, so compare against the base branch.
  const compareRef = isCreatingBranch ? baseBranch : targetBranch || undefined
  const outcome = getWorkspaceSyncPushOutcome({
    mode,
    targetBranch,
    defaultBranch: baseBranch,
    isCreatingBranch,
  })
  const {
    preview,
    previewOperationId,
    previewDiffCount,
    previewIsLoading,
    previewError,
    refetchPreview,
  } = useWorkspaceSyncExportPreview(workspaceId, {
    push: {
      message,
      branch: targetBranch,
      create_pr: outcome.createPr,
      pr_base_branch: baseBranch,
      include_schedules: false,
    },
    compareRef,
    provider,
  })
  // Direct pushes to the sync base or repository default are off.
  const blockedBranch = getBlockedPushBranch({
    targetBranch,
    baseBranch,
    branches: repoBranches,
  })
  const isBlocked = blockedBranch !== undefined
  const reviewRequest = getReviewRequestLabel(provider)
  const reviewRequestShort = getReviewRequestAbbreviation(provider)
  const visiblePreview = previewRequested ? preview : undefined
  const changeCount = visiblePreview ? previewDiffCount : 0
  // Pushes to the repository default are blocked by name, so stay off until
  // the branch list says which branch that is.
  const defaultKnown = repoBranches?.some((candidate) => candidate.is_default)
  const pushDisabled =
    !previewOperationId ||
    !previewRequested ||
    previewIsLoading ||
    exportWorkspaceIsPending ||
    branchesIsLoading ||
    !baseBranch ||
    !defaultKnown ||
    isBlocked ||
    targetBranch === "" ||
    message.trim() === ""

  function resetPreview() {
    setPreviewRequested(false)
  }
  useEffect(() => {
    resetPreview()
  }, [
    compareRef,
    persistedGitUrl,
    provider,
    syncRevision,
    targetBranch,
    mode,
    message,
  ])
  function handlePreview() {
    if (!compareRef || targetBranch === "" || message.trim() === "") return
    setPreviewRequested(true)
    refetchPreview()
  }

  async function handlePush() {
    try {
      if (pushDisabled || !previewOperationId) return
      const result = await exportWorkspace({ operationId: previewOperationId })
      writeLastPushBranch(rememberScope, targetBranch)
      // The new branch now exists, so later previews compare against it.
      if (isCreatingBranch) {
        selectBranch(targetBranch)
      }
      // The push changed the remote: drop the old preview and refetch the
      // branches and commits so a new branch is listed and can be restored.
      resetPreview()
      for (const key of [
        "workflow-sync-branches",
        "repository_commits",
        "workspace-sync-export-preview",
      ]) {
        void queryClient.invalidateQueries({ queryKey: [key, workspaceId] })
      }
      const prUrl = result.commit.pr_url
      toast({
        title: prUrl
          ? `${reviewRequestShort} ready`
          : `Pushed to ${targetBranch}`,
        description: result.commit.message ?? result.commit.sha ?? undefined,
        action: prUrl ? (
          <ToastAction
            asChild
            altText={`Open ${reviewRequest}`}
            className="gap-1.5"
          >
            <a href={prUrl} target="_blank" rel="noopener noreferrer">
              View {reviewRequestShort}
              <ExternalLinkIcon className="size-3.5" />
            </a>
          </ToastAction>
        ) : undefined,
      })
    } catch (error) {
      if (error instanceof SyncOperationWaitCancelled) return
      toast({
        title: "Push failed",
        description: (
          <ExportErrorMessage error={error} workspaceId={workspaceId} />
        ),
        variant: "destructive",
      })
    }
  }

  return (
    <>
      <div className="flex flex-1 flex-col">
        <div className="px-4 py-3">
          <WorkspacePushExplanation />
        </div>
        {isBlocked && (
          <GitSyncNotice tone="warning">
            Pushing to {blockedBranch} directly is off. Pick or create another
            branch.
          </GitSyncNotice>
        )}
        {previewError && (
          <GitSyncNotice tone="error">
            Could not preview this push:{" "}
            <ExportErrorMessage
              announce={false}
              error={previewError}
              workspaceId={workspaceId}
            />
          </GitSyncNotice>
        )}
        {visiblePreview ? (
          <GitSyncChangeList
            workspaceId={workspaceId}
            operationId={previewOperationId}
            diffCount={previewDiffCount}
            direction="push"
            summary={
              visiblePreview.resource_diffs?.length
                ? getPushPreviewSummary(
                    visiblePreview.resource_diffs,
                    compareRef
                  )
                : `${previewDiffCount} change(s), compared with ${compareRef ?? "the branch"}`
            }
            resources={visiblePreview.resources ?? []}
            diffs={visiblePreview.resource_diffs ?? []}
            fileCount={visiblePreview.files.length}
          />
        ) : (
          <GitSyncPreviewEmpty
            description={`Preview to skim what this push includes, compared with ${compareRef ?? "the branch"}.`}
            isLoading={previewIsLoading}
            disabled={!compareRef}
            onPreview={() => void handlePreview()}
          />
        )}
      </div>

      <GitSyncActionBar label="Push actions">
        <span className="text-xs text-muted-foreground">Into</span>
        <GitSyncBranchPicker
          branches={repoBranches}
          branch={targetBranch}
          isCreatingBranch={isCreatingBranch}
          baseBranch={baseBranch}
          isLoading={branchesIsLoading}
          hasError={Boolean(branchesError)}
          disabled={exportWorkspaceIsPending}
          placement="footer"
          className="w-80 shrink-0"
          onCreateBranch={createBranch}
          onSelectBranch={selectBranch}
        />
        <Input
          aria-label="Commit message"
          placeholder="Commit message"
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          className="h-8 min-w-48 flex-1"
        />
        {visiblePreview && (
          <GitSyncPreviewButton
            hasPreview
            isLoading={previewIsLoading}
            disabled={!compareRef}
            onPreview={() => void handlePreview()}
          />
        )}
        <PushSplitButton
          label={getPushButtonLabel({
            isBlocked,
            isPending: exportWorkspaceIsPending,
            createPr: outcome.createPr,
            changeCount,
            targetBranch,
            reviewRequestShort,
          })}
          mode={mode}
          onModeChange={setMode}
          isPending={exportWorkspaceIsPending}
          disabled={pushDisabled}
          menuDisabled={isBlocked || exportWorkspaceIsPending}
          reviewRequest={reviewRequest}
          reviewRequestShort={reviewRequestShort}
          onPush={() => void handlePush()}
        />
      </GitSyncActionBar>
    </>
  )
}

/**
 * Primary push action with a chevron menu to choose between opening a pull
 * request and pushing only.
 */
function PushSplitButton({
  label,
  mode,
  onModeChange,
  isPending,
  disabled,
  menuDisabled,
  reviewRequest,
  reviewRequestShort,
  onPush,
}: {
  label: string
  mode: WorkspaceSyncPushMode
  onModeChange: (mode: WorkspaceSyncPushMode) => void
  isPending: boolean
  disabled: boolean
  menuDisabled: boolean
  reviewRequest: string
  reviewRequestShort: string
  onPush: () => void
}) {
  return (
    <div className="flex">
      <Button
        type="button"
        size="sm"
        onClick={onPush}
        disabled={disabled}
        className="shrink-0 gap-1.5 rounded-r-none"
      >
        <PushButtonIcon
          isPending={isPending}
          createPr={mode === "pull-request"}
        />
        {label}
      </Button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            size="sm"
            aria-label="Push options"
            disabled={menuDisabled}
            className="rounded-l-none border-l border-primary-foreground/30 px-2"
          >
            <ChevronDownIcon className="size-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" side="top" className="w-72">
          <DropdownMenuRadioGroup
            value={mode}
            onValueChange={(value) =>
              onModeChange(value as WorkspaceSyncPushMode)
            }
          >
            <DropdownMenuRadioItem value="pull-request" className="items-start">
              <span className="flex flex-col gap-0.5">
                <span className="font-medium">
                  Push and open {reviewRequestShort}
                </span>
                <span className="text-xs text-muted-foreground">
                  Review and merge happen in the {reviewRequest}
                </span>
              </span>
            </DropdownMenuRadioItem>
            <DropdownMenuRadioItem value="direct" className="items-start">
              <span className="flex flex-col gap-0.5">
                <span className="font-medium">Push only</span>
                <span className="text-xs text-muted-foreground">
                  Commits to the branch, no {reviewRequest}
                </span>
              </span>
            </DropdownMenuRadioItem>
          </DropdownMenuRadioGroup>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}

function PushButtonIcon({
  isPending,
  createPr,
}: {
  isPending: boolean
  createPr: boolean
}) {
  if (isPending) {
    return <Loader2Icon className="size-4 animate-spin" />
  }
  if (createPr) {
    return <GitPullRequestIcon className="size-4" />
  }
  return <ArrowUpIcon className="size-4" />
}

function getBlockedPushBranch({
  targetBranch,
  baseBranch,
  branches,
}: {
  targetBranch: string
  baseBranch: string | undefined
  branches: GitBranchInfo[] | undefined
}): string | undefined {
  if (!targetBranch) {
    return undefined
  }
  if (targetBranch === baseBranch) {
    return baseBranch
  }
  const isRepositoryDefault = branches?.some(
    (candidate) => candidate.is_default && candidate.name === targetBranch
  )
  return isRepositoryDefault ? targetBranch : undefined
}

function getPushButtonLabel({
  isBlocked,
  isPending,
  createPr,
  changeCount,
  targetBranch,
  reviewRequestShort,
}: {
  isBlocked: boolean
  isPending: boolean
  createPr: boolean
  changeCount: number
  targetBranch: string
  reviewRequestShort: string
}): string {
  if (isPending) {
    return "Pushing..."
  }
  if (isBlocked) {
    return "Pick another branch"
  }
  const push = changeCount > 0 ? `Push ${changeCount}` : "Push"
  if (createPr) {
    return `${push} and open ${reviewRequestShort}`
  }
  return `${push} to ${targetBranch}`
}

function getPushPreviewSummary(
  diffs: PullResourceDiff[],
  compareRef: string | undefined
): string {
  const ref = compareRef ?? "the branch"
  const counts = formatChangeCounts(diffs)
  return counts
    ? `${counts}, compared with ${ref}`
    : `No changes compared with ${ref}`
}
