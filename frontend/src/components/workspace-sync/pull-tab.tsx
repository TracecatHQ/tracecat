"use client"

import {
  AlertTriangleIcon,
  ArrowDownIcon,
  CheckCircle2Icon,
  Loader2Icon,
  SearchIcon,
  XCircleIcon,
} from "lucide-react"
import { useEffect, useState } from "react"
import type { GitCommitInfo, PullResult, VcsProvider } from "@/client"
import { CommitSelector } from "@/components/registry/commit-selector"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { toast } from "@/components/ui/use-toast"
import { PullResourceManifest } from "@/components/workspace-sync/push-resource-manifest"
import { ResourceDiffSection } from "@/components/workspace-sync/resource-diff-review"
import {
  getPullResultTotals,
  getWorkspaceSyncResourceLabel,
  workspaceSyncResourceCountEntries,
} from "@/components/workspace-sync/resource-metadata"
import { useWorkflowSync } from "@/hooks/use-workspace-sync"
import { getApiErrorDetail } from "@/lib/errors"
import { cn } from "@/lib/utils"

interface WorkspaceSyncPullTabProps {
  workspaceId: string
  provider: VcsProvider
  commits: GitCommitInfo[] | undefined
  commitsIsLoading: boolean
  commitsError: Error | null
}

/**
 * Pull composer: pick a commit to import from, preview the incoming resource
 * diff, then apply it to overwrite the workspace with that snapshot.
 */
export function WorkspaceSyncPullTab({
  workspaceId,
  provider,
  commits,
  commitsIsLoading,
  commitsError,
}: WorkspaceSyncPullTabProps) {
  const { pullWorkflows, pullWorkflowsIsPending } = useWorkflowSync(workspaceId)

  const [selectedCommitSha, setSelectedCommitSha] = useState<string | null>(
    null
  )
  const [syncSchedules, setSyncSchedules] = useState(false)
  const [pullPreview, setPullPreview] = useState<PullResult | null>(null)
  const [pullPreviewOptions, setPullPreviewOptions] = useState<{
    commitSha: string
    syncSchedules: boolean
  } | null>(null)
  const [pullResult, setPullResult] = useState<PullResult | null>(null)
  const [pullAction, setPullAction] = useState<"preview" | "apply" | null>(null)

  const effectivePullSha = selectedCommitSha ?? commits?.[0]?.sha
  const pullPreviewMatchesSelection =
    Boolean(effectivePullSha) &&
    pullPreviewOptions !== null &&
    pullPreviewOptions.commitSha === effectivePullSha &&
    pullPreviewOptions.syncSchedules === syncSchedules
  const canApplyPull =
    pullPreviewMatchesSelection && pullPreview?.success === true

  // Default the pull source to HEAD once commits load.
  useEffect(() => {
    if (commits?.length && !selectedCommitSha) {
      setSelectedCommitSha(commits[0].sha)
    }
  }, [commits, selectedCommitSha])

  useEffect(() => {
    setPullPreview(null)
    setPullPreviewOptions(null)
    setPullResult(null)
  }, [effectivePullSha, provider, syncSchedules])

  async function handlePreviewPull() {
    if (!effectivePullSha) {
      return
    }

    setPullAction("preview")
    setPullPreview(null)
    setPullResult(null)
    try {
      const result = await pullWorkflows({
        commit_sha: effectivePullSha,
        dry_run: true,
        sync_schedules: syncSchedules,
      })
      setPullPreview(result)
      setPullPreviewOptions({
        commitSha: effectivePullSha,
        syncSchedules,
      })
      toast({
        title: result.success ? "Pull preview ready" : "Pull preview failed",
        description: result.message,
        variant: result.success ? undefined : "destructive",
      })
    } catch (error) {
      toast({
        title: "Pull preview failed",
        description: getApiErrorDetail(error) ?? "Request failed",
        variant: "destructive",
      })
    } finally {
      setPullAction(null)
    }
  }

  async function handleApplyPull() {
    if (!effectivePullSha || !canApplyPull) {
      return
    }

    setPullAction("apply")
    setPullResult(null)
    try {
      const result = await pullWorkflows({
        commit_sha: effectivePullSha,
        sync_schedules: syncSchedules,
      })
      setPullResult(result)
      if (result.success) {
        setPullPreview(null)
        setPullPreviewOptions(null)
      }
      toast({
        title: result.success
          ? "Workspace pull completed"
          : "Workspace pull failed",
        description: result.message,
        variant: result.success ? undefined : "destructive",
      })
    } catch (error) {
      toast({
        title: "Pull operation failed",
        description: getApiErrorDetail(error) ?? "Request failed",
        variant: "destructive",
      })
    } finally {
      setPullAction(null)
    }
  }

  return (
    <>
      <div className="space-y-2">
        <div className="flex flex-wrap items-center gap-3">
          <Label className="shrink-0">Pull from commit</Label>
          <CommitSelector
            commits={commits}
            currentCommitSha={selectedCommitSha}
            isLoading={commitsIsLoading}
            error={commitsError}
            onSelectCommit={setSelectedCommitSha}
            disabled={pullWorkflowsIsPending}
          />
        </div>
        <p className="text-[11px] text-muted-foreground">
          Choosing an older commit updates matching resources from that
          snapshot.
        </p>
      </div>

      <label className="flex items-center gap-2 text-sm">
        <Checkbox
          checked={syncSchedules}
          onCheckedChange={(checked) => setSyncSchedules(checked === true)}
          disabled={pullWorkflowsIsPending}
        />
        Overwrite schedules
      </label>

      <SyncWarning>
        Preview the incoming resource diff before applying. Existing resources
        with the same ID will be overwritten. Schedules are preserved unless
        checked above.
      </SyncWarning>

      {pullPreview && pullPreviewMatchesSelection && (
        <PullPreviewSummary result={pullPreview} />
      )}
      {pullResult && <PullResultSummary result={pullResult} />}
      {!(pullPreview && pullPreviewMatchesSelection) && !pullResult && (
        <PullEmptyState />
      )}

      <div className="flex flex-col gap-3 border-t pt-4 sm:flex-row sm:items-center sm:justify-between">
        <div className="flex flex-wrap items-center gap-1.5 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
          <ArrowDownIcon className="size-3.5" />
          <span>Importing into this workspace from</span>
          <span className="font-mono text-foreground">
            {effectivePullSha?.substring(0, 7) ?? "—"}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={handlePreviewPull}
            disabled={
              pullWorkflowsIsPending || commitsIsLoading || !effectivePullSha
            }
            className="shrink-0 gap-1.5"
          >
            {pullWorkflowsIsPending && pullAction === "preview" ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <SearchIcon className="size-4" />
            )}
            {pullWorkflowsIsPending && pullAction === "preview"
              ? "Previewing..."
              : "Preview changes"}
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={handleApplyPull}
            disabled={pullWorkflowsIsPending || !canApplyPull}
            className="shrink-0 gap-1.5"
          >
            {pullWorkflowsIsPending && pullAction === "apply" ? (
              <Loader2Icon className="size-4 animate-spin" />
            ) : (
              <ArrowDownIcon className="size-4" />
            )}
            {pullWorkflowsIsPending && pullAction === "apply"
              ? "Applying..."
              : "Apply pull"}
          </Button>
        </div>
      </div>
    </>
  )
}

/**
 * Inline amber advisory used for pull overwrite consequences.
 */
function SyncWarning({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-start gap-2 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-700">
      <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" />
      <span>{children}</span>
    </div>
  )
}

/**
 * Placeholder shown in the pull view before a preview has been generated,
 * nudging the user to preview the incoming diff before applying.
 */
function PullEmptyState() {
  return (
    <div className="flex flex-col items-center gap-2 rounded-md border border-dashed px-6 py-10 text-center">
      <SearchIcon className="size-5 text-muted-foreground" />
      <p className="text-sm font-medium text-foreground">No preview yet</p>
      <p className="max-w-xs text-xs text-muted-foreground">
        Preview changes first to review the incoming resource diff before
        applying the pull.
      </p>
    </div>
  )
}

/**
 * Dry-run pull preview: a compact summary line plus a reviewable list of
 * per-resource file diffs.
 */
function PullPreviewSummary({ result }: { result: PullResult }) {
  const { found: totalFound } = getPullResultTotals(result)
  const resourceDiffs = result.resource_diffs ?? []
  const addedCount = resourceDiffs.filter(
    (diff) => diff.change_type === "added"
  ).length
  const modifiedCount = resourceDiffs.filter(
    (diff) => diff.change_type === "modified"
  ).length

  return (
    <div className="space-y-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <span className="flex items-center gap-1.5 text-sm font-medium">
          {result.success ? (
            <CheckCircle2Icon className="size-4 text-green-600" />
          ) : (
            <XCircleIcon className="size-4 text-destructive" />
          )}
          {result.success ? "Pull preview" : "Preview failed"}
        </span>
        <span className="h-4 w-px bg-border" />
        <SummaryMetric label="found" value={totalFound} />
        <SummaryMetric
          label="changes"
          value={resourceDiffs.length}
          emphasize={resourceDiffs.length > 0}
        />
        <SummaryMetric
          label="issues"
          value={result.diagnostics.length}
          emphasize={result.diagnostics.length > 0}
        />
        <div className="ml-auto flex flex-wrap gap-1.5">
          <Badge variant="secondary" className="font-normal">
            {addedCount} added
          </Badge>
          <Badge variant="secondary" className="font-normal">
            {modifiedCount} modified
          </Badge>
        </div>
      </div>

      {!result.success && (
        <p className="text-sm text-muted-foreground">{result.message}</p>
      )}

      <PullResourceManifest result={result} />

      <ResourceDiffSection diffs={resourceDiffs} />

      {result.diagnostics.length > 0 && (
        <PullDiagnostics diagnostics={result.diagnostics} />
      )}
    </div>
  )
}

/**
 * Single inline metric ("13 found") for the pull preview summary line.
 */
function SummaryMetric({
  label,
  value,
  emphasize = false,
}: {
  label: string
  value: number
  emphasize?: boolean
}) {
  return (
    <span className="text-sm tabular-nums">
      <span className={cn("font-medium", emphasize && "text-amber-600")}>
        {value}
      </span>{" "}
      <span className="text-muted-foreground">{label}</span>
    </span>
  )
}

/**
 * Shared diagnostic list for pull previews and completed pulls.
 */
function PullDiagnostics({
  diagnostics,
}: {
  diagnostics: PullResult["diagnostics"]
}) {
  return (
    <div className="space-y-2">
      <h6 className="text-sm font-medium">Issues found:</h6>
      <div className="max-h-32 space-y-2 overflow-y-auto">
        {diagnostics.map((diagnostic, index) => (
          <div
            key={[
              diagnostic.workflow_path,
              diagnostic.workflow_title,
              diagnostic.error_type,
              diagnostic.message,
              index,
            ].join(":")}
            className="flex items-start gap-2 rounded bg-muted p-2 text-xs"
          >
            <AlertTriangleIcon className="mt-0.5 size-3 shrink-0 text-amber-500" />
            <div className="min-w-0 space-y-1">
              <div className="font-medium">
                {diagnostic.workflow_title || diagnostic.workflow_path}
              </div>
              <div className="text-muted-foreground">{diagnostic.message}</div>
              <Badge variant="outline" className="text-xs">
                {diagnostic.error_type}
              </Badge>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

/**
 * Compact summary of a completed pull: counts, per-resource breakdown, and any
 * diagnostics.
 */
function PullResultSummary({ result }: { result: PullResult }) {
  const resourceCounts = workspaceSyncResourceCountEntries(result)
  const { found: totalFound, imported: totalImported } =
    getPullResultTotals(result)

  return (
    <div className="space-y-3 rounded-lg border p-4">
      <div className="flex items-center gap-2">
        {result.success ? (
          <CheckCircle2Icon className="size-4 text-green-600" />
        ) : (
          <XCircleIcon className="size-4 text-destructive" />
        )}
        <h5 className="text-sm font-medium">
          {result.success ? "Pull completed" : "Pull failed"}
        </h5>
      </div>

      <div className="grid grid-cols-3 gap-4 text-sm">
        <div>
          <span className="text-muted-foreground">Found:</span>
          <span className="ml-1 font-medium">{totalFound}</span>
        </div>
        <div>
          <span className="text-muted-foreground">Imported:</span>
          <span className="ml-1 font-medium text-green-600">
            {totalImported}
          </span>
        </div>
        <div>
          <span className="text-muted-foreground">Issues:</span>
          <span className="ml-1 font-medium text-amber-600">
            {result.diagnostics.length}
          </span>
        </div>
      </div>

      {resourceCounts.length > 0 && (
        <div className="grid grid-cols-2 gap-2 text-xs sm:grid-cols-3">
          {resourceCounts.map(([resourceType, count]) => (
            <div
              key={resourceType}
              className="rounded-md border bg-muted/30 px-2 py-1.5"
            >
              <div className="font-medium">
                {getWorkspaceSyncResourceLabel(resourceType)}
              </div>
              <div className="text-muted-foreground">
                {count.imported}/{count.found}
              </div>
            </div>
          ))}
        </div>
      )}

      <p className="text-sm">{result.message}</p>

      {result.diagnostics.length > 0 && (
        <PullDiagnostics diagnostics={result.diagnostics} />
      )}
    </div>
  )
}
