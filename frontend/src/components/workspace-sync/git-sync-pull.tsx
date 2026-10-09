"use client"

import {
  AlertTriangleIcon,
  ArrowDownIcon,
  CheckIcon,
  InfoIcon,
  Loader2Icon,
} from "lucide-react"
import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react"
import type {
  CatalogMappingRequirement,
  GitCommitInfo,
  McpIntegrationMappingRequirement,
  PullResourceDiff,
  PullResult,
  SecretStoreMappingRequirement,
  VcsProvider,
} from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { GitSyncActionBar } from "@/components/workspace-sync/git-sync-action-bar"
import {
  countChangedResources,
  formatChangeCounts,
  GitSyncChangeList,
  GitSyncNotice,
  GitSyncPreviewButton,
  GitSyncPreviewEmpty,
} from "@/components/workspace-sync/git-sync-change-list"
import { GitSyncCommitPicker } from "@/components/workspace-sync/git-sync-commit-picker"
import {
  CatalogMappingRequirements,
  catalogMappingSelections,
  isCatalogRequirementMatched,
  isMcpRequirementMatched,
  McpIntegrationMappingRequirements,
  mcpIntegrationMappingSelections,
  SecretStoreMappingRequirements,
  secretStoreMappingSelections,
} from "@/components/workspace-sync/mapping-requirements-card"
import {
  getWorkspaceSyncResourceAbbr,
  getWorkspaceSyncResourceLabel,
} from "@/components/workspace-sync/resource-metadata"
import { useWorkflowSync } from "@/hooks/use-workspace-sync"
import { getApiErrorDetail } from "@/lib/errors"
import { cn } from "@/lib/utils"

interface GitSyncPullTabProps {
  workspaceId: string
  workspaceName: string
  provider: VcsProvider
  baseBranch: string | undefined
  commits: GitCommitInfo[] | undefined
  commitsIsLoading: boolean
  commitsError: Error | null
}

/**
 * Pull composer: a commit goes into the workspace. Preview lists what comes
 * in, and the pull applies only a preview that matches the current choices.
 */
export function GitSyncPullTab({
  workspaceId,
  workspaceName,
  provider,
  baseBranch,
  commits,
  commitsIsLoading,
  commitsError,
}: GitSyncPullTabProps) {
  const { pullWorkflows, pullWorkflowsIsPending } = useWorkflowSync(workspaceId)

  const [selectedCommitSha, setSelectedCommitSha] = useState<string | null>(
    null
  )
  const [syncSchedules, setSyncSchedules] = useState(false)
  const [catalogMappings, setCatalogMappings] = useState<
    Record<string, string>
  >({})
  const [catalogMappingRequirements, setCatalogMappingRequirements] = useState<
    CatalogMappingRequirement[]
  >([])
  const [mcpMappings, setMcpMappings] = useState<Record<string, string>>({})
  const [mcpMappingRequirements, setMcpMappingRequirements] = useState<
    McpIntegrationMappingRequirement[]
  >([])
  const [storeMappings, setStoreMappings] = useState<Record<string, string>>({})
  const [storeMappingRequirements, setStoreMappingRequirements] = useState<
    SecretStoreMappingRequirement[]
  >([])
  const [pullPreview, setPullPreview] = useState<PullResult | null>(null)
  const [pullPreviewOptions, setPullPreviewOptions] = useState<{
    commitSha: string
    syncSchedules: boolean
    catalogMappingsKey: string | null
    mcpMappingsKey: string | null
    storeMappingsKey: string | null
  } | null>(null)
  const [pullResult, setPullResult] = useState<PullResult | null>(null)
  const [pulledAt, setPulledAt] = useState<Date | null>(null)
  const [pullAction, setPullAction] = useState<"preview" | "apply" | null>(null)

  const effectivePullSha = selectedCommitSha ?? commits?.[0]?.sha
  const selectedCatalogMappings = useMemo(
    () => catalogMappingSelections(catalogMappings),
    [catalogMappings]
  )
  const catalogMappingsKey = useMemo(
    () => JSON.stringify(selectedCatalogMappings),
    [selectedCatalogMappings]
  )
  const selectedMcpMappings = useMemo(
    () => mcpIntegrationMappingSelections(mcpMappings),
    [mcpMappings]
  )
  const mcpMappingsKey = useMemo(
    () => JSON.stringify(selectedMcpMappings),
    [selectedMcpMappings]
  )
  const selectedStoreMappings = useMemo(
    () => secretStoreMappingSelections(storeMappings),
    [storeMappings]
  )
  const storeMappingsKey = useMemo(
    () => JSON.stringify(selectedStoreMappings),
    [selectedStoreMappings]
  )
  const pullPreviewMatchesSource =
    Boolean(effectivePullSha) &&
    pullPreviewOptions !== null &&
    pullPreviewOptions.commitSha === effectivePullSha &&
    pullPreviewOptions.syncSchedules === syncSchedules
  // Every selection set must match what the backend last validated. Changing
  // any one invalidates the preview until it is re-run.
  const pullPreviewMatchesSelection =
    pullPreviewMatchesSource &&
    pullPreviewOptions?.catalogMappingsKey === catalogMappingsKey &&
    pullPreviewOptions?.mcpMappingsKey === mcpMappingsKey &&
    pullPreviewOptions?.storeMappingsKey === storeMappingsKey
  const canApplyPull =
    pullPreviewMatchesSelection && pullPreview?.success === true
  const isPreviewing = pullWorkflowsIsPending && pullAction === "preview"
  const isApplying = pullWorkflowsIsPending && pullAction === "apply"
  const showPreview = Boolean(pullPreview && pullPreviewMatchesSource)
  const changeCount = showPreview
    ? countChangedResources(pullPreview?.resource_diffs ?? [])
    : 0
  const shortSha = effectivePullSha?.substring(0, 7)

  const resetPullPreview = useCallback(() => {
    setPullPreview(null)
    setPullPreviewOptions(null)
    setPullResult(null)
  }, [])

  // Default the pull source to HEAD once commits load.
  useEffect(() => {
    if (commits?.length && !selectedCommitSha) {
      setSelectedCommitSha(commits[0].sha)
    }
  }, [commits, selectedCommitSha])

  useEffect(() => {
    resetPullPreview()
    setCatalogMappings({})
    setCatalogMappingRequirements([])
    setMcpMappings({})
    setMcpMappingRequirements([])
    setStoreMappings({})
    setStoreMappingRequirements([])
  }, [effectivePullSha, provider, resetPullPreview])

  useEffect(() => {
    resetPullPreview()
  }, [syncSchedules, resetPullPreview])

  async function handlePreviewPull(matches?: {
    catalog: Record<string, string>
    mcp: Record<string, string>
    store: Record<string, string>
  }) {
    if (!effectivePullSha) {
      return
    }
    const catalogSelections = matches
      ? catalogMappingSelections(matches.catalog)
      : selectedCatalogMappings
    const mcpSelections = matches
      ? mcpIntegrationMappingSelections(matches.mcp)
      : selectedMcpMappings
    const storeSelections = matches
      ? secretStoreMappingSelections(matches.store)
      : selectedStoreMappings

    setPullAction("preview")
    setPullPreview(null)
    setPullResult(null)
    try {
      const result = await pullWorkflows({
        commit_sha: effectivePullSha,
        dry_run: true,
        sync_schedules: syncSchedules,
        catalog_mappings: catalogSelections,
        mcp_integration_mappings: mcpSelections,
        secret_store_mappings: storeSelections,
      })
      setPullPreview(result)
      // A preview with matches stops listing them; keep them so the page can
      // say what was matched and reopen the choices.
      const nextCatalog = result.catalog_mapping_requirements ?? []
      const nextMcp = result.mcp_integration_mapping_requirements ?? []
      setCatalogMappingRequirements((previous) =>
        nextCatalog.length > 0 || catalogSelections.length === 0
          ? nextCatalog
          : previous
      )
      setMcpMappingRequirements((previous) =>
        nextMcp.length > 0 || mcpSelections.length === 0 ? nextMcp : previous
      )
      const nextStore = result.secret_store_mapping_requirements ?? []
      setStoreMappingRequirements((previous) =>
        nextStore.length > 0 || storeSelections.length === 0
          ? nextStore
          : previous
      )
      setPullPreviewOptions({
        commitSha: effectivePullSha,
        syncSchedules,
        catalogMappingsKey: JSON.stringify(catalogSelections),
        mcpMappingsKey: JSON.stringify(mcpSelections),
        storeMappingsKey: JSON.stringify(storeSelections),
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
        catalog_mappings: selectedCatalogMappings,
        mcp_integration_mappings: selectedMcpMappings,
        secret_store_mappings: selectedStoreMappings,
      })
      if (result.success) {
        // The apply result only counts resources; names come from the preview.
        setPullResult({
          ...result,
          resources:
            result.resources ??
            (pullPreview ? getPreviewResources(pullPreview) : undefined),
        })
        setPulledAt(new Date())
        setPullPreview(null)
        setPullPreviewOptions(null)
        setCatalogMappings({})
        setCatalogMappingRequirements([])
        setMcpMappings({})
        setMcpMappingRequirements([])
        setStoreMappings({})
        setStoreMappingRequirements([])
      } else {
        setPullPreview(result)
        setCatalogMappingRequirements(result.catalog_mapping_requirements ?? [])
        setMcpMappingRequirements(
          result.mcp_integration_mapping_requirements ?? []
        )
        setStoreMappingRequirements(
          result.secret_store_mapping_requirements ?? []
        )
        setPullPreviewOptions({
          commitSha: effectivePullSha,
          syncSchedules,
          // Apply revalidates catalog and MCP integration access, so a failure
          // invalidates the prior preview even if the selections are unchanged.
          catalogMappingsKey: null,
          mcpMappingsKey: null,
          storeMappingsKey: null,
        })
      }
      toast({
        title: result.success
          ? `Pulled into ${workspaceName}`
          : "Workspace pull failed",
        description: result.message,
        variant: result.success ? undefined : "destructive",
      })
    } catch (error) {
      toast({
        title: "Pull failed",
        description: getApiErrorDetail(error) ?? "Request failed",
        variant: "destructive",
      })
    } finally {
      setPullAction(null)
    }
  }

  function handleSaveMatches(matches: MatchDrafts) {
    setCatalogMappings(matches.catalog)
    setMcpMappings(matches.mcp)
    setStoreMappings(matches.store)
    void handlePreviewPull(matches)
  }

  return (
    <>
      <div className="flex min-w-0 flex-1 flex-col">
        {pullPreview && showPreview && !isPreviewing ? (
          <PullPreview
            workspaceId={workspaceId}
            result={pullPreview}
            summary={getPullPreviewSummary(
              pullPreview.resource_diffs ?? [],
              shortSha,
              baseBranch
            )}
            workspaceName={workspaceName}
            catalogMappingRequirements={catalogMappingRequirements}
            catalogMappings={catalogMappings}
            mcpMappingRequirements={mcpMappingRequirements}
            mcpMappings={mcpMappings}
            storeMappingRequirements={storeMappingRequirements}
            storeMappings={storeMappings}
            onSaveMatches={handleSaveMatches}
            disabled={pullWorkflowsIsPending}
          />
        ) : pullResult && !isPreviewing ? (
          <PullResultSummary
            result={pullResult}
            workspaceName={workspaceName}
            baseBranch={baseBranch}
            pulledAt={pulledAt}
          />
        ) : (
          <GitSyncPreviewEmpty
            description={`Preview to see what comes into ${workspaceName} before pulling.`}
            isLoading={isPreviewing}
            disabled={
              pullWorkflowsIsPending || commitsIsLoading || !effectivePullSha
            }
            onPreview={() => void handlePreviewPull()}
          />
        )}
      </div>

      <GitSyncActionBar label="Pull actions">
        <span className="text-xs text-muted-foreground">From</span>
        <GitSyncCommitPicker
          commits={commits}
          commitSha={effectivePullSha}
          isLoading={commitsIsLoading}
          hasError={Boolean(commitsError)}
          disabled={pullWorkflowsIsPending}
          side="top"
          className="w-[26rem] max-w-full"
          onSelectCommit={setSelectedCommitSha}
        />
        <OverwriteSchedules
          checked={syncSchedules}
          disabled={pullWorkflowsIsPending}
          onCheckedChange={setSyncSchedules}
        />
        <span className="flex-1" />
        {(showPreview || pullResult) && (
          <GitSyncPreviewButton
            hasPreview
            isLoading={isPreviewing}
            disabled={
              pullWorkflowsIsPending || commitsIsLoading || !effectivePullSha
            }
            onPreview={() => void handlePreviewPull()}
          />
        )}
        <Button
          type="button"
          size="sm"
          onClick={() => void handleApplyPull()}
          disabled={pullWorkflowsIsPending || !canApplyPull}
          className="shrink-0 gap-1.5"
        >
          {isApplying ? (
            <Loader2Icon className="size-4 animate-spin" />
          ) : (
            <ArrowDownIcon className="size-4" />
          )}
          {getPullButtonLabel({ isApplying, changeCount, workspaceName })}
        </Button>
      </GitSyncActionBar>
    </>
  )
}

/** Overwrite schedules checkbox; what it overwrites is in the tooltip. */
function OverwriteSchedules({
  checked,
  disabled,
  onCheckedChange,
}: {
  checked: boolean
  disabled: boolean
  onCheckedChange: (checked: boolean) => void
}) {
  return (
    <span className="flex shrink-0 items-center gap-1.5">
      <label className="flex items-center gap-2 text-sm">
        <Checkbox
          checked={checked}
          onCheckedChange={(value) => onCheckedChange(value === true)}
          disabled={disabled}
        />
        Overwrite schedules
      </label>
      <TooltipProvider delayDuration={150}>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              aria-label="About overwriting schedules"
              className="flex text-muted-foreground hover:text-foreground"
            >
              <InfoIcon className="size-3.5" />
            </button>
          </TooltipTrigger>
          <TooltipContent className="max-w-64">
            {checked
              ? "Existing resources with matching IDs are overwritten, including their schedules."
              : "Existing resources with matching IDs are overwritten. Schedules are preserved."}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    </span>
  )
}

/** Resources a preview lists, or ones derived from its diffs when it has none. */
function getPreviewResources(result: PullResult) {
  return (
    result.resources ??
    (result.resource_diffs ?? []).map((diff) => ({
      resource_type: diff.resource_type,
      source_id: diff.source_id,
      name: diff.title ?? diff.source_id,
      path: diff.source_path,
    }))
  )
}

function getPullPreviewSummary(
  diffs: PullResourceDiff[],
  shortSha: string | undefined,
  baseBranch: string | undefined
): string {
  const commit = shortSha ?? "this commit"
  const source = baseBranch ? `${commit} on ${baseBranch}` : commit
  const counts = formatChangeCounts(diffs)
  return counts ? `${counts} from ${source}` : `Already matches ${source}`
}

function getPullButtonLabel({
  isApplying,
  changeCount,
  workspaceName,
}: {
  isApplying: boolean
  changeCount: number
  workspaceName: string
}): string {
  if (isApplying) {
    return "Pulling..."
  }
  if (changeCount > 0) {
    return `Pull ${changeCount} into ${workspaceName}`
  }
  return `Pull into ${workspaceName}`
}

/**
 * Dry-run pull preview: any failure, mapping choices, what comes in, and
 * diagnostics.
 */
function PullPreview({
  workspaceId,
  workspaceName,
  result,
  catalogMappingRequirements,
  catalogMappings,
  mcpMappingRequirements,
  mcpMappings,
  storeMappingRequirements,
  storeMappings,
  onSaveMatches,
  disabled,
  summary,
}: {
  workspaceId: string
  workspaceName: string
  result: PullResult
  catalogMappingRequirements: CatalogMappingRequirement[]
  catalogMappings: Record<string, string>
  mcpMappingRequirements: McpIntegrationMappingRequirement[]
  mcpMappings: Record<string, string>
  storeMappingRequirements: SecretStoreMappingRequirement[]
  storeMappings: Record<string, string>
  onSaveMatches: (matches: MatchDrafts) => void
  disabled: boolean
  summary: string
}) {
  // Secret stores are optional, so they never count toward a blocked pull.
  const matchCount =
    catalogMappingRequirements.length + mcpMappingRequirements.length
  const unmatchedCount =
    catalogMappingRequirements.filter(
      (requirement) =>
        !isCatalogRequirementMatched(requirement, catalogMappings)
    ).length +
    mcpMappingRequirements.filter(
      (requirement) => !isMcpRequirementMatched(requirement, mcpMappings)
    ).length
  const [matchesOpen, setMatchesOpen] = useState(unmatchedCount > 0)
  const linkedStoreCount = storeMappingRequirements.filter((requirement) =>
    requirement.candidates.some(
      (candidate) =>
        candidate.store_id === storeMappings[requirement.source_store]
    )
  ).length
  const librarySkillInstalls = result.library_skill_installs ?? []
  const resourceDiffs = result.resource_diffs ?? []
  const resources = getPreviewResources(result)
  const matchedNames = [
    ...catalogMappingRequirements.map((requirement) => requirement.model_name),
    ...mcpMappingRequirements.map(
      (requirement) =>
        requirement.name ??
        requirement.slug ??
        requirement.source_mcp_integration_id
    ),
  ]
  const chooseMatches = (
    <Button
      type="button"
      variant="outline"
      size="sm"
      className="ml-auto h-7 shrink-0 text-xs text-foreground"
      onClick={() => setMatchesOpen(true)}
    >
      Choose matches
    </Button>
  )

  let notice: ReactNode = null
  if (unmatchedCount > 0) {
    notice = (
      <GitSyncNotice tone="warning">
        <span className="flex items-center gap-3">
          <span>
            Pull is blocked: {unmatchedCount}{" "}
            {unmatchedCount === 1
              ? "reference in this commit needs"
              : "references in this commit need"}{" "}
            a match in {workspaceName}.
          </span>
          {chooseMatches}
        </span>
      </GitSyncNotice>
    )
  } else if (!result.success) {
    notice = (
      <GitSyncNotice tone="warning">
        <span className="flex items-center gap-3">
          <span>{result.message || "Pull preview failed."}</span>
          {matchCount > 0 && chooseMatches}
        </span>
      </GitSyncNotice>
    )
  } else if (matchCount > 0) {
    notice = (
      <div
        role="status"
        className="flex items-center gap-2 border-b px-5 py-2.5 text-xs text-muted-foreground"
      >
        <CheckIcon className="size-3.5 shrink-0 text-green-700 dark:text-green-500" />
        <span>Matches checked for {matchedNames.join(", ")}.</span>
        <button
          type="button"
          className="text-foreground underline underline-offset-2"
          onClick={() => setMatchesOpen(true)}
        >
          Change
        </button>
      </div>
    )
  }

  return (
    <div className="flex min-w-0 flex-col">
      {notice}
      {storeMappingRequirements.length > 0 && (
        <div
          role="status"
          className="flex items-center gap-2 border-b px-5 py-2.5 text-xs text-muted-foreground"
        >
          <InfoIcon className="size-3.5 shrink-0" />
          <span>
            Secret stores: {linkedStoreCount} of{" "}
            {storeMappingRequirements.length} linked. Unlinked secrets import
            without a store and can be linked later in Credentials.
          </span>
          <button
            type="button"
            className="text-foreground underline underline-offset-2"
            onClick={() => setMatchesOpen(true)}
          >
            Choose stores
          </button>
        </div>
      )}
      {librarySkillInstalls.length > 0 && (
        <div
          role="status"
          className="flex items-center gap-2 border-b px-5 py-2.5 text-xs text-muted-foreground"
        >
          <InfoIcon className="size-3.5 shrink-0" />
          <span>
            Pulling installs {librarySkillInstalls.length}{" "}
            {librarySkillInstalls.length === 1 ? "skill" : "skills"} from the
            skills library: {librarySkillInstalls.join(", ")}.
          </span>
        </div>
      )}
      {/* A failed preview compared nothing, so "Already matches" would mislead. */}
      {(result.success || resourceDiffs.length > 0) && (
        <GitSyncChangeList
          workspaceId={workspaceId}
          direction="pull"
          summary={summary}
          resources={resources}
          diffs={resourceDiffs}
          fileCount={result.files?.length ?? resourceDiffs.length}
        />
      )}
      {result.diagnostics.length > 0 && (
        <PullDiagnostics diagnostics={result.diagnostics} />
      )}
      {matchCount + storeMappingRequirements.length > 0 && (
        <MatchesDialog
          open={matchesOpen}
          onOpenChange={setMatchesOpen}
          workspaceName={workspaceName}
          catalogMappingRequirements={catalogMappingRequirements}
          catalogMappings={catalogMappings}
          mcpMappingRequirements={mcpMappingRequirements}
          mcpMappings={mcpMappings}
          storeMappingRequirements={storeMappingRequirements}
          storeMappings={storeMappings}
          disabled={disabled}
          onSave={(matches) => {
            setMatchesOpen(false)
            onSaveMatches(matches)
          }}
        />
      )}
    </div>
  )
}

/** Draft match choices for one pull, keyed by source reference. */
interface MatchDrafts {
  catalog: Record<string, string>
  mcp: Record<string, string>
  store: Record<string, string>
}

/**
 * Chooses what each unmatched model, MCP integration or secret store in the
 * commit uses in this workspace. Edits a draft; saving runs the preview again.
 */
function MatchesDialog({
  open,
  onOpenChange,
  workspaceName,
  catalogMappingRequirements,
  catalogMappings,
  mcpMappingRequirements,
  mcpMappings,
  storeMappingRequirements,
  storeMappings,
  disabled,
  onSave,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  workspaceName: string
  catalogMappingRequirements: CatalogMappingRequirement[]
  catalogMappings: Record<string, string>
  mcpMappingRequirements: McpIntegrationMappingRequirement[]
  mcpMappings: Record<string, string>
  storeMappingRequirements: SecretStoreMappingRequirement[]
  storeMappings: Record<string, string>
  disabled: boolean
  onSave: (matches: MatchDrafts) => void
}) {
  const [catalogDraft, setCatalogDraft] = useState(catalogMappings)
  const [mcpDraft, setMcpDraft] = useState(mcpMappings)
  const [storeDraft, setStoreDraft] = useState(storeMappings)
  const total =
    catalogMappingRequirements.length + mcpMappingRequirements.length
  const matched =
    catalogMappingRequirements.filter((requirement) =>
      isCatalogRequirementMatched(requirement, catalogDraft)
    ).length +
    mcpMappingRequirements.filter((requirement) =>
      isMcpRequirementMatched(requirement, mcpDraft)
    ).length
  const remaining = total - matched

  useEffect(() => {
    if (open) {
      setCatalogDraft(catalogMappings)
      setMcpDraft(mcpMappings)
      setStoreDraft(storeMappings)
    }
  }, [open, catalogMappings, mcpMappings, storeMappings])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-lg">
        <DialogTitle>
          {remaining > 0
            ? `Match ${remaining} ${remaining === 1 ? "reference" : "references"} to pull`
            : "Matches for this pull"}
        </DialogTitle>
        <DialogDescription className="-mt-2">
          {total > 0
            ? `This commit uses references that Tracecat can't match to ${workspaceName} on its own. Choose what each should use here.`
            : `This commit uses secret stores that Tracecat can't match to ${workspaceName} on its own.`}
          {storeMappingRequirements.length > 0 &&
            " Secret stores are optional: unlinked secrets import without a store."}
        </DialogDescription>
        <div className="divide-y border-y">
          {catalogMappingRequirements.length > 0 && (
            <CatalogMappingRequirements
              requirements={catalogMappingRequirements}
              selections={catalogDraft}
              onChange={(source, target) =>
                setCatalogDraft((current) => ({ ...current, [source]: target }))
              }
              disabled={disabled}
            />
          )}
          {mcpMappingRequirements.length > 0 && (
            <McpIntegrationMappingRequirements
              requirements={mcpMappingRequirements}
              selections={mcpDraft}
              onChange={(source, target) =>
                setMcpDraft((current) => ({ ...current, [source]: target }))
              }
              disabled={disabled}
            />
          )}
          {storeMappingRequirements.length > 0 && (
            <SecretStoreMappingRequirements
              requirements={storeMappingRequirements}
              selections={storeDraft}
              onChange={(source, target) =>
                setStoreDraft((current) => ({ ...current, [source]: target }))
              }
              disabled={disabled}
            />
          )}
        </div>
        <DialogFooter className="items-center sm:justify-between">
          <span className="text-xs text-muted-foreground">
            {total > 0 ? `${matched} of ${total} matched` : "Optional"}
          </span>
          <div className="flex gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={remaining > 0 || disabled}
              onClick={() =>
                onSave({
                  catalog: catalogDraft,
                  mcp: mcpDraft,
                  store: storeDraft,
                })
              }
            >
              Save and preview again
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/**
 * Diagnostics as full-width rows under a "Needs attention" strip, shared by the
 * preview and the result.
 */
function PullDiagnostics({
  diagnostics,
}: {
  diagnostics: PullResult["diagnostics"]
}) {
  return (
    <div className="flex flex-col">
      <div className="flex h-9 items-center gap-1.5 border-b bg-muted/40 px-5 text-xs text-muted-foreground">
        <span className="font-medium text-foreground">Needs attention</span>
        <span aria-hidden>·</span>
        <span>
          {diagnostics.length} {diagnostics.length === 1 ? "issue" : "issues"}
        </span>
      </div>
      <ul>
        {diagnostics.map((diagnostic, index) => (
          <li
            key={[
              diagnostic.workflow_path,
              diagnostic.workflow_title,
              diagnostic.error_type,
              diagnostic.message,
              index,
            ].join(":")}
            className="flex min-h-11 items-center gap-2.5 border-b px-5 py-2 text-sm"
          >
            <AlertTriangleIcon className="size-3.5 shrink-0 text-amber-600" />
            <span className="w-56 shrink-0 truncate font-medium">
              {diagnostic.workflow_title || diagnostic.workflow_path}
            </span>
            <span className="min-w-0 flex-1 text-muted-foreground">
              {diagnostic.message}
            </span>
            <Badge
              variant="secondary"
              className="shrink-0 rounded px-1.5 text-[11px] font-normal"
            >
              {diagnostic.error_type}
            </Badge>
          </li>
        ))}
      </ul>
    </div>
  )
}

/**
 * What a completed pull did: one line, any issues, then a row per resource
 * type with how many landed.
 */
function PullResultSummary({
  result,
  workspaceName,
  baseBranch,
  pulledAt,
}: {
  result: PullResult
  workspaceName: string
  baseBranch: string | undefined
  pulledAt: Date | null
}) {
  const resourceCounts = pullResultCountEntries(result)
  const { found, imported } = getPullResultTotals(result)
  const isPartial = imported < found || result.diagnostics.length > 0
  const headline =
    imported < found
      ? `Pulled ${imported} of ${found} into ${workspaceName}`
      : `Pulled ${imported} into ${workspaceName}`
  const shortSha = result.commit_sha.substring(0, 7)

  return (
    <div className="flex min-w-0 flex-col">
      <div
        role="status"
        className="flex flex-wrap items-center gap-x-2.5 gap-y-1 border-b px-5 py-3"
      >
        {isPartial ? (
          <AlertTriangleIcon className="size-4 shrink-0 text-amber-600" />
        ) : (
          <CheckIcon className="size-4 shrink-0 text-green-700 dark:text-green-500" />
        )}
        <span className="text-sm font-medium">{headline}</span>
        <span className="text-xs text-muted-foreground">
          from <span className="font-mono">{shortSha}</span>
          {baseBranch && ` on ${baseBranch}`}
          {pulledAt &&
            ` · ${pulledAt.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" })}`}
        </span>
      </div>
      {(result.library_skill_installs ?? []).length > 0 && (
        <div className="flex items-center gap-2 border-b px-5 py-2.5 text-xs text-muted-foreground">
          <InfoIcon className="size-3.5 shrink-0" />
          <span>
            Installed from the skills library:{" "}
            {(result.library_skill_installs ?? []).join(", ")}.
          </span>
        </div>
      )}
      {result.diagnostics.length > 0 && (
        <PullDiagnostics diagnostics={result.diagnostics} />
      )}
      {resourceCounts.length > 0 && (
        <>
          <div className="flex h-9 items-center gap-1.5 border-b bg-muted/40 px-5 text-xs text-muted-foreground">
            <span className="font-medium text-foreground">What changed</span>
            <span aria-hidden>·</span>
            <span>
              {imported} of {found} imported
            </span>
          </div>
          <ul>
            {resourceCounts.map(([resourceType, count]) => (
              <li
                key={resourceType}
                className="flex h-10 items-center gap-2.5 border-b px-5 text-sm"
              >
                <span className="flex size-[22px] shrink-0 items-center justify-center rounded bg-muted font-mono text-[9px] font-semibold text-muted-foreground">
                  {getWorkspaceSyncResourceAbbr(resourceType)}
                </span>
                <span className="w-40 shrink-0 truncate font-medium">
                  {getWorkspaceSyncResourceLabel(resourceType)}
                </span>
                <span className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
                  {resourceNames(result, resourceType)}
                </span>
                <span
                  className={cn(
                    "shrink-0 font-mono text-xs",
                    count.imported < count.found
                      ? "text-amber-700 dark:text-amber-500"
                      : "text-muted-foreground"
                  )}
                >
                  {count.imported} of {count.found}
                </span>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

/** Names of the resources of one type in a pull result, when it lists them. */
function resourceNames(result: PullResult, resourceType: string): string {
  const names = (result.resources ?? [])
    .filter((resource) => resource.resource_type === resourceType)
    .map((resource) => resource.name)
  if (names.length <= 3) {
    return names.join(", ")
  }
  return `${names.slice(0, 3).join(", ")} +${names.length - 3} more`
}

/** Non-empty per-resource pull counts in stable order. */
function pullResultCountEntries(result: PullResult) {
  return Object.entries(result.resource_counts ?? {})
    .filter(([, count]) => count.found > 0 || count.imported > 0)
    .sort(([left], [right]) => left.localeCompare(right))
}

/** Totals across resource types, or the legacy workflow-only counters. */
function getPullResultTotals(result: PullResult): {
  found: number
  imported: number
} {
  const entries = pullResultCountEntries(result)
  if (entries.length === 0) {
    return {
      found: result.workflows_found ?? 0,
      imported: result.workflows_imported ?? 0,
    }
  }
  return {
    found: entries.reduce((total, [, count]) => total + count.found, 0),
    imported: entries.reduce((total, [, count]) => total + count.imported, 0),
  }
}
