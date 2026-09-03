"use client"

import {
  AlertTriangleIcon,
  ArrowDownIcon,
  CheckCircle2Icon,
  Loader2Icon,
  SearchIcon,
  XCircleIcon,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import type {
  CatalogMappingCandidate,
  CatalogMappingRequirement,
  CatalogMappingSelection,
  GitCommitInfo,
  McpIntegrationMappingRequirement,
  McpIntegrationMappingSelection,
  PullResult,
  VcsProvider,
} from "@/client"
import { CommitSelector } from "@/components/registry/commit-selector"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { toast } from "@/components/ui/use-toast"
import {
  type MappingRequirementItem,
  MappingRequirementsCard,
  mappingAffectsSummary,
} from "@/components/workspace-sync/mapping-requirements-card"
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
  const [pullPreview, setPullPreview] = useState<PullResult | null>(null)
  const [pullPreviewOptions, setPullPreviewOptions] = useState<{
    commitSha: string
    syncSchedules: boolean
    catalogMappingsKey: string | null
    mcpMappingsKey: string | null
  } | null>(null)
  const [pullResult, setPullResult] = useState<PullResult | null>(null)
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
  const pullPreviewMatchesSource =
    Boolean(effectivePullSha) &&
    pullPreviewOptions !== null &&
    pullPreviewOptions.commitSha === effectivePullSha &&
    pullPreviewOptions.syncSchedules === syncSchedules
  // Both selection sets must match what the backend last validated. Changing
  // either one invalidates the preview until it is re-run.
  const pullPreviewMatchesSelection =
    pullPreviewMatchesSource &&
    pullPreviewOptions?.catalogMappingsKey === catalogMappingsKey &&
    pullPreviewOptions?.mcpMappingsKey === mcpMappingsKey
  const canApplyPull =
    pullPreviewMatchesSelection && pullPreview?.success === true

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
  }, [effectivePullSha, provider, resetPullPreview])

  useEffect(() => {
    resetPullPreview()
  }, [syncSchedules, resetPullPreview])

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
        catalog_mappings: selectedCatalogMappings,
        mcp_integration_mappings: selectedMcpMappings,
      })
      setPullPreview(result)
      setCatalogMappingRequirements(result.catalog_mapping_requirements ?? [])
      setMcpMappingRequirements(
        result.mcp_integration_mapping_requirements ?? []
      )
      setPullPreviewOptions({
        commitSha: effectivePullSha,
        syncSchedules,
        catalogMappingsKey,
        mcpMappingsKey,
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
        catalog_mappings: selectedCatalogMappings,
        mcp_integration_mappings: selectedMcpMappings,
      })
      if (result.success) {
        setPullResult(result)
        setPullPreview(null)
        setPullPreviewOptions(null)
        setCatalogMappings({})
        setCatalogMappingRequirements([])
        setMcpMappings({})
        setMcpMappingRequirements([])
      } else {
        setPullPreview(result)
        setCatalogMappingRequirements(result.catalog_mapping_requirements ?? [])
        setMcpMappingRequirements(
          result.mcp_integration_mapping_requirements ?? []
        )
        setPullPreviewOptions({
          commitSha: effectivePullSha,
          syncSchedules,
          // Apply revalidates catalog and MCP integration access. A failure
          // invalidates the prior preview even when the selected UUIDs
          // themselves did not change.
          catalogMappingsKey: null,
          mcpMappingsKey: null,
        })
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

  function handleCatalogMappingChange(
    sourceCatalogId: string,
    targetCatalogId: string
  ) {
    setCatalogMappings((current) => ({
      ...current,
      [sourceCatalogId]: targetCatalogId,
    }))
    setPullResult(null)
  }

  function handleMcpMappingChange(
    sourceMcpIntegrationId: string,
    targetMcpIntegrationId: string
  ) {
    setMcpMappings((current) => ({
      ...current,
      [sourceMcpIntegrationId]: targetMcpIntegrationId,
    }))
    setPullResult(null)
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

      {pullPreview && pullPreviewMatchesSource && (
        <PullPreviewSummary
          result={pullPreview}
          catalogMappingRequirements={catalogMappingRequirements}
          catalogMappings={catalogMappings}
          onCatalogMappingChange={handleCatalogMappingChange}
          mcpMappingRequirements={mcpMappingRequirements}
          mcpMappings={mcpMappings}
          onMcpMappingChange={handleMcpMappingChange}
          mappingsMatchPreview={pullPreviewMatchesSelection}
          disabled={pullWorkflowsIsPending}
        />
      )}
      {pullResult && <PullResultSummary result={pullResult} />}
      {!(pullPreview && pullPreviewMatchesSource) && !pullResult && (
        <PullEmptyState />
      )}

      <div
        role="group"
        aria-label="Pull actions"
        className="sticky bottom-0 z-10 flex min-w-0 flex-col gap-3 border-t bg-background py-4 after:pointer-events-none after:absolute after:inset-x-0 after:top-full after:h-8 after:bg-background after:content-[''] sm:flex-row sm:items-center sm:justify-between"
      >
        <div className="flex min-w-0 flex-wrap items-center gap-1.5 rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
          <ArrowDownIcon className="size-3.5" />
          <span>Importing into this workspace from</span>
          <span className="font-mono text-foreground">
            {effectivePullSha?.substring(0, 7) ?? "—"}
          </span>
        </div>
        <div className="flex shrink-0 items-center gap-2">
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
    <Alert
      variant="warning"
      className="rounded-md px-3 py-2 text-[11px] [&>svg]:left-3 [&>svg]:top-2.5 [&>svg~*]:pl-5"
    >
      <AlertTriangleIcon className="size-3.5" />
      <AlertDescription className="text-[11px]">{children}</AlertDescription>
    </Alert>
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
function PullPreviewSummary({
  result,
  catalogMappingRequirements,
  catalogMappings,
  onCatalogMappingChange,
  mcpMappingRequirements,
  mcpMappings,
  onMcpMappingChange,
  mappingsMatchPreview,
  disabled,
}: {
  result: PullResult
  catalogMappingRequirements: CatalogMappingRequirement[]
  catalogMappings: Record<string, string>
  onCatalogMappingChange: (
    sourceCatalogId: string,
    targetCatalogId: string
  ) => void
  mcpMappingRequirements: McpIntegrationMappingRequirement[]
  mcpMappings: Record<string, string>
  onMcpMappingChange: (
    sourceMcpIntegrationId: string,
    targetMcpIntegrationId: string
  ) => void
  mappingsMatchPreview: boolean
  disabled: boolean
}) {
  const { found: totalFound } = getPullResultTotals(result)
  const resourceDiffs = result.resource_diffs ?? []
  const addedCount = resourceDiffs.filter(
    (diff) => diff.change_type === "added"
  ).length
  const modifiedCount = resourceDiffs.filter(
    (diff) => diff.change_type === "modified"
  ).length

  return (
    <div className="min-w-0 space-y-3">
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

      {catalogMappingRequirements.length > 0 && (
        <CatalogMappingRequirements
          requirements={catalogMappingRequirements}
          selections={catalogMappings}
          onChange={onCatalogMappingChange}
          mappingsMatchPreview={mappingsMatchPreview}
          disabled={disabled}
        />
      )}

      {mcpMappingRequirements.length > 0 && (
        <McpIntegrationMappingRequirements
          requirements={mcpMappingRequirements}
          selections={mcpMappings}
          onChange={onMcpMappingChange}
          mappingsMatchPreview={mappingsMatchPreview}
          disabled={disabled}
        />
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
 * Inline resolution UI for source models with more than one safe target match.
 */
function CatalogMappingRequirements({
  requirements,
  selections,
  onChange,
  mappingsMatchPreview,
  disabled,
}: {
  requirements: CatalogMappingRequirement[]
  selections: Record<string, string>
  onChange: (sourceCatalogId: string, targetCatalogId: string) => void
  mappingsMatchPreview: boolean
  disabled: boolean
}) {
  const items = useMemo(
    () =>
      requirements.map((requirement): MappingRequirementItem => {
        const baseLabels = requirement.candidates.map(
          catalogMappingCandidateBaseLabel
        )
        const baseLabelCounts = new Map<string, number>()
        for (const baseLabel of baseLabels) {
          baseLabelCounts.set(
            baseLabel,
            (baseLabelCounts.get(baseLabel) ?? 0) + 1
          )
        }
        // Only disambiguate with a catalog id fragment when two candidates
        // would otherwise render the same label.
        const candidates = requirement.candidates.map((candidate, index) => {
          const baseLabel = baseLabels[index]
          const isDuplicate = (baseLabelCounts.get(baseLabel) ?? 0) > 1
          return {
            value: candidate.catalog_id,
            label: isDuplicate
              ? `${baseLabel} · ${candidate.catalog_id.slice(0, 8)}`
              : baseLabel,
          }
        })
        return {
          key: requirement.source_catalog_id,
          title: requirement.model_name,
          subtitle: requirement.model_provider,
          ariaLabel: `Target model for ${requirement.model_name}`,
          candidates,
          affects: mappingAffectsSummary(requirement),
        }
      }),
    [requirements]
  )

  return (
    <MappingRequirementsCard
      heading="Choose target models"
      description="These source models match multiple or changed target providers. Your choices apply to every listed preset version and workflow action in this pull."
      placeholder="Choose target model"
      items={items}
      selections={selections}
      onChange={onChange}
      mappingsMatchPreview={mappingsMatchPreview}
      disabled={disabled}
    />
  )
}

function catalogMappingCandidateBaseLabel(
  candidate: CatalogMappingCandidate
): string {
  const details = [candidate.provider_name]
  if (
    candidate.model_display_name &&
    candidate.model_display_name !== candidate.model_name
  ) {
    details.push(candidate.model_display_name)
  }
  if (candidate.endpoint_hostname) {
    details.push(candidate.endpoint_hostname)
  }
  return details.join(" · ")
}

/**
 * Inline resolution UI for imported MCP integration references that could not
 * be resolved against a workspace-local integration.
 */
function McpIntegrationMappingRequirements({
  requirements,
  selections,
  onChange,
  mappingsMatchPreview,
  disabled,
}: {
  requirements: McpIntegrationMappingRequirement[]
  selections: Record<string, string>
  onChange: (
    sourceMcpIntegrationId: string,
    targetMcpIntegrationId: string
  ) => void
  mappingsMatchPreview: boolean
  disabled: boolean
}) {
  const items = useMemo(
    () =>
      requirements.map((requirement): MappingRequirementItem => {
        const nameCounts = new Map<string, number>()
        for (const candidate of requirement.candidates) {
          nameCounts.set(
            candidate.name,
            (nameCounts.get(candidate.name) ?? 0) + 1
          )
        }
        // Slugs are workspace-unique, so only append one when two candidates
        // would otherwise render the same name.
        const candidates = requirement.candidates.map((candidate) => {
          const isDuplicate = (nameCounts.get(candidate.name) ?? 0) > 1
          const name = isDuplicate
            ? `${candidate.name} (${candidate.slug})`
            : candidate.name
          return {
            value: candidate.mcp_integration_id,
            label: `${name} (${candidate.server_type} · ${candidate.auth_type})`,
          }
        })
        return {
          key: requirement.source_mcp_integration_id,
          title:
            requirement.name ??
            requirement.slug ??
            requirement.source_mcp_integration_id,
          subtitle: requirement.message,
          ariaLabel: `Target MCP integration for ${
            requirement.slug ?? requirement.source_mcp_integration_id
          }`,
          candidates,
          affects: mappingAffectsSummary(requirement),
        }
      }),
    [requirements]
  )

  return (
    <MappingRequirementsCard
      heading="Choose target MCP integrations"
      description="These source MCP integrations could not be matched automatically. Your choices apply to every listed preset version and workflow action in this pull."
      placeholder="Choose target MCP integration"
      items={items}
      selections={selections}
      onChange={onChange}
      mappingsMatchPreview={mappingsMatchPreview}
      disabled={disabled}
    />
  )
}

function catalogMappingSelections(
  mappings: Record<string, string>
): CatalogMappingSelection[] {
  return Object.entries(mappings)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([sourceCatalogId, targetCatalogId]) => ({
      source_catalog_id: sourceCatalogId,
      target_catalog_id: targetCatalogId,
    }))
}

function mcpIntegrationMappingSelections(
  mappings: Record<string, string>
): McpIntegrationMappingSelection[] {
  return Object.entries(mappings)
    .sort(([left], [right]) => left.localeCompare(right))
    .map(([sourceMcpIntegrationId, targetMcpIntegrationId]) => ({
      source_mcp_integration_id: sourceMcpIntegrationId,
      target_mcp_integration_id: targetMcpIntegrationId,
    }))
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
