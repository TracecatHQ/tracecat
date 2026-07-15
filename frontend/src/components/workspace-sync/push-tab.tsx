"use client"

import { ArrowUpIcon, GitPullRequestIcon, Loader2Icon } from "lucide-react"
import { useEffect, useState } from "react"
import type { GitBranchInfo, VcsProvider } from "@/client"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { toast } from "@/components/ui/use-toast"
import {
  useWorkspaceSyncBranchTarget,
  WorkspaceSyncBranchSelector,
} from "@/components/workspace-sync/branch-target-selector"
import { getWorkspaceSyncPushErrorNotice } from "@/components/workspace-sync/push-error"
import {
  getReviewRequestLabel,
  getWorkspaceSyncPushButtonLabel,
  getWorkspaceSyncPushOutcome,
  getWorkspaceSyncPushResultLabel,
  getWorkspaceSyncPushWarning,
  WorkspaceSyncPushWarning,
} from "@/components/workspace-sync/push-target-policy"
import { PushResourcePreview } from "@/components/workspace-sync/resource-diff-review"
import {
  useWorkspaceSyncExport,
  useWorkspaceSyncExportPreview,
} from "@/hooks/use-workspace-sync"
import { getApiErrorDetail } from "@/lib/errors"

interface WorkspaceSyncPushTabProps {
  workspaceId: string
  persistedGitUrl: string | undefined
  provider: VcsProvider
  repoDisplayName: string | null
  repoBranches: GitBranchInfo[] | undefined
  baseBranch: string | undefined
  branchesIsLoading: boolean
  branchesError: unknown
}

/**
 * Push composer: commit message, push mode, branch target, an on-demand resource
 * preview, and the push action that opens a pull request or commits directly.
 */
export function WorkspaceSyncPushTab({
  workspaceId,
  persistedGitUrl,
  provider,
  repoDisplayName,
  repoBranches,
  baseBranch,
  branchesIsLoading,
  branchesError,
}: WorkspaceSyncPushTabProps) {
  const { exportWorkspace, exportWorkspaceIsPending } =
    useWorkspaceSyncExport(workspaceId)

  const [exportMessage, setExportMessage] = useState("Export workspace config")
  const [exportPreviewRequested, setExportPreviewRequested] = useState(false)

  const {
    branch: exportBranch,
    setBranch: setExportBranch,
    isCreatingBranch,
    selectBranch: selectExportBranch,
    hasBranches,
  } = useWorkspaceSyncBranchTarget({
    branches: repoBranches,
    newBranchPrefix: "sync/workspace",
  })

  const targetBranch = exportBranch.trim()
  const exportCompareRef = isCreatingBranch
    ? baseBranch
    : targetBranch || undefined
  const {
    preview: exportPreview,
    previewIsLoading: exportPreviewIsLoading,
    previewError: exportPreviewError,
    refetchPreview: refetchExportPreview,
  } = useWorkspaceSyncExportPreview(workspaceId, {
    compareRef: exportCompareRef,
    provider,
    enabled: false,
  })
  const visibleExportPreview = exportPreviewRequested
    ? exportPreview
    : undefined
  const visibleExportPreviewIsLoading =
    exportPreviewRequested && exportPreviewIsLoading
  const exportPreviewErrorMessage = exportPreviewError
    ? (getApiErrorDetail(exportPreviewError) ?? "Request failed")
    : undefined
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
  const reviewRequestTitle =
    getReviewRequestLabel(provider) === "merge request"
      ? "Merge request"
      : "Pull request"
  const exportDisabled =
    exportWorkspaceIsPending ||
    branchesIsLoading ||
    (!hasBranches && !isCreatingBranch) ||
    pushOutcome.isPullRequestBlocked ||
    targetBranch === "" ||
    exportMessage.trim() === ""

  useEffect(() => {
    setExportPreviewRequested(false)
  }, [exportCompareRef, persistedGitUrl, provider])

  async function onExport() {
    try {
      const result = await exportWorkspace({
        message: exportMessage,
        branch: targetBranch,
        create_pr: pushOutcome.createPr,
        include_schedules: false,
      })
      toast({
        title: result.commit.pr_url
          ? `${reviewRequestTitle} ready`
          : "Workspace config pushed",
        description:
          result.commit.pr_url ?? result.commit.sha ?? result.commit.message,
      })
    } catch (error) {
      const notice = getWorkspaceSyncPushErrorNotice(error)
      toast({
        title: notice.title,
        description: notice.description,
        variant: notice.isDestructive ? "destructive" : "default",
      })
    }
  }

  function handlePreviewExport() {
    if (!exportCompareRef) {
      return
    }
    setExportPreviewRequested(true)
    void refetchExportPreview()
  }

  return (
    <>
      <div className="space-y-2">
        <Label htmlFor="workspace-sync-message">Commit message</Label>
        <Input
          id="workspace-sync-message"
          value={exportMessage}
          onChange={(event) => setExportMessage(event.target.value)}
        />
      </div>

      <div className="space-y-2">
        <Label htmlFor="workspace-sync-branch">Branch</Label>
        <WorkspaceSyncBranchSelector
          id="workspace-sync-branch"
          branches={repoBranches}
          branch={exportBranch}
          isCreatingBranch={isCreatingBranch}
          branchesIsLoading={branchesIsLoading}
          hasBranches={hasBranches}
          branchesError={branchesError}
          newBranchPlaceholder="sync/workspace"
          onSelectBranch={selectExportBranch}
          onBranchChange={setExportBranch}
        />
        <WorkspaceSyncPushWarning
          warning={pushWarning}
          blocked={pushOutcome.isPullRequestBlocked}
        />
      </div>

      <PushResourcePreview
        preview={visibleExportPreview}
        isLoading={visibleExportPreviewIsLoading}
        compareRef={exportCompareRef}
        errorMessage={
          exportPreviewRequested ? exportPreviewErrorMessage : undefined
        }
        hasRequestedPreview={exportPreviewRequested}
        onRequestPreview={handlePreviewExport}
      />

      <div className="flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-1.5 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
          <ArrowUpIcon className="size-3.5" />
          <span>Pushing to</span>
          <span className="font-mono text-foreground">
            {repoDisplayName ?? "repository"}
          </span>
          <span>@</span>
          <span className="font-mono text-foreground">
            {targetBranch || "—"}
          </span>
          <span>·</span>
          <span className="text-foreground">
            {getWorkspaceSyncPushResultLabel({
              outcome: pushOutcome,
              defaultBranch: baseBranch,
              provider,
            })}
          </span>
        </div>
        <Button
          type="button"
          size="sm"
          onClick={onExport}
          disabled={exportDisabled}
          className="shrink-0 gap-1.5"
        >
          {exportWorkspaceIsPending ? (
            <Loader2Icon className="size-4 animate-spin" />
          ) : pushOutcome.createPr ? (
            <GitPullRequestIcon className="size-4" />
          ) : (
            <ArrowUpIcon className="size-4" />
          )}
          {getWorkspaceSyncPushButtonLabel({
            outcome: pushOutcome,
            isCreatingBranch,
            isPending: exportWorkspaceIsPending,
            provider,
          })}
        </Button>
      </div>
    </>
  )
}
