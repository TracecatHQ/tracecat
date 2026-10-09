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
import { GitSyncActionBar } from "@/components/workspace-sync/git-sync-action-bar"
import { GitSyncBranchPicker } from "@/components/workspace-sync/git-sync-branch-picker"
import {
  countChangedResources,
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
import {
  useWorkspaceSyncExport,
  useWorkspaceSyncExportPreview,
} from "@/hooks/use-workspace-sync"
import { getApiErrorDetail } from "@/lib/errors"
import { useQueryClient } from "@/lib/query"

interface GitSyncPushTabProps {
  workspaceId: string
  persistedGitUrl: string
  provider: VcsProvider
  repoBranches: GitBranchInfo[] | undefined
  baseBranch: string | undefined
  branchesIsLoading: boolean
  branchesError: unknown
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
}: GitSyncPushTabProps) {
  const { exportWorkspace, exportWorkspaceIsPending } =
    useWorkspaceSyncExport(workspaceId)
  const queryClient = useQueryClient()

  const [message, setMessage] = useState("Export workspace config")
  const [mode, setMode] = useState<WorkspaceSyncPushMode>("pull-request")
  const [previewedAt, setPreviewedAt] = useState<Date | null>(null)

  const { branch, isCreatingBranch, selectBranch, createBranch, hasBranches } =
    useWorkspaceSyncBranchTarget({
      branches: repoBranches,
      newBranchPrefix: "sync/workspace",
      rememberForWorkspaceId: workspaceId,
    })

  const targetBranch = branch.trim()
  // A new branch does not exist yet, so compare against the base branch.
  const compareRef = isCreatingBranch ? baseBranch : targetBranch || undefined
  const { preview, previewIsLoading, previewError, refetchPreview } =
    useWorkspaceSyncExportPreview(workspaceId, {
      compareRef,
      provider,
      enabled: false,
    })
  const outcome = getWorkspaceSyncPushOutcome({
    mode,
    targetBranch,
    defaultBranch: baseBranch,
    isCreatingBranch,
  })
  // Direct pushes to the default branch are off, so both modes are blocked.
  const isBlocked = outcome.targetIsDefault
  const reviewRequest = getReviewRequestLabel(provider)
  const reviewRequestShort = getReviewRequestAbbreviation(provider)
  const visiblePreview = previewedAt ? preview : undefined
  const changeCount = countChangedResources(
    visiblePreview?.resource_diffs ?? []
  )
  // Without the base branch, a push to the default branch can't be blocked.
  const pushDisabled =
    exportWorkspaceIsPending ||
    branchesIsLoading ||
    !baseBranch ||
    (!hasBranches && !isCreatingBranch) ||
    isBlocked ||
    targetBranch === "" ||
    message.trim() === ""

  useEffect(() => {
    setPreviewedAt(null)
  }, [compareRef, persistedGitUrl, provider])

  async function handlePreview() {
    if (!compareRef) {
      return
    }
    const result = await refetchPreview()
    setPreviewedAt(result.error ? null : new Date())
  }

  async function handlePush() {
    try {
      const result = await exportWorkspace({
        message,
        branch: targetBranch,
        create_pr: outcome.createPr,
        include_schedules: false,
      })
      writeLastPushBranch(workspaceId, targetBranch)
      // The push changed the remote: drop the old preview and refetch the
      // branches and commits so a new branch is listed and can be restored.
      setPreviewedAt(null)
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
      toast({
        title: "Push failed",
        description: getApiErrorDetail(error) ?? "Request failed",
        variant: "destructive",
      })
    }
  }

  return (
    <>
      <div className="flex flex-1 flex-col">
        {isBlocked && (
          <GitSyncNotice tone="warning">
            Pushing to {baseBranch} directly is off. Pick or create another
            branch.
          </GitSyncNotice>
        )}
        {previewError && (
          <GitSyncNotice tone="error">
            Could not preview this push:{" "}
            {getApiErrorDetail(previewError) ?? "Request failed"}
          </GitSyncNotice>
        )}
        {visiblePreview ? (
          <GitSyncChangeList
            workspaceId={workspaceId}
            direction="push"
            summary={getPushPreviewSummary(
              visiblePreview.resource_diffs ?? [],
              compareRef
            )}
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
