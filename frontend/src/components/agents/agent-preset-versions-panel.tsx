"use client"

import { ArrowLeft, GitCompareArrows, History, RotateCcw } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type {
  AgentPresetRead,
  AgentPresetVersionDiff,
  AgentPresetVersionReadMinimal,
} from "@/client"
import { AgentPresetPromptDiff } from "@/components/agents/agent-preset-prompt-diff"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  useAgentPresetVersions,
  useCompareAgentPresetVersions,
  useRestoreAgentPresetVersion,
} from "@/hooks/use-agent-presets"
import { getRelativeTime } from "@/lib/event-history"

type VersionsPanelView = "history" | "compare"

function stringifyDiffValue(value: unknown): string {
  if (value === null || value === undefined) {
    return "-"
  }
  if (typeof value === "string") {
    return value
  }
  return JSON.stringify(value, null, 2)
}

function getVersionName(
  version: AgentPresetVersionReadMinimal | undefined
): string {
  return version ? `v${version.version}` : "Unknown version"
}

interface AgentPresetVersionsPanelProps {
  workspaceId: string
  preset: AgentPresetRead | null
}

export function AgentPresetVersionsPanel({
  workspaceId,
  preset,
}: AgentPresetVersionsPanelProps) {
  const [view, setView] = useState<VersionsPanelView>("history")
  const [baseVersionId, setBaseVersionId] = useState<string | null>(null)
  const [compareToId, setCompareToId] = useState<string | null>(null)

  useEffect(() => {
    setView("history")
    setBaseVersionId(null)
    setCompareToId(null)
  }, [preset?.id])

  const presetId = preset?.id ?? null
  const currentVersionId = preset?.current_version_id ?? null
  const { versions, versionsIsLoading, versionsError } = useAgentPresetVersions(
    workspaceId,
    presetId,
    { enabled: Boolean(presetId) }
  )
  const { diff, diffIsLoading } = useCompareAgentPresetVersions(
    workspaceId,
    presetId,
    baseVersionId,
    compareToId,
    { enabled: view === "compare" }
  )
  const { restoreAgentPresetVersion, restoreAgentPresetVersionIsPending } =
    useRestoreAgentPresetVersion(workspaceId)

  const versionById = useMemo(() => {
    return new Map((versions ?? []).map((version) => [version.id, version]))
  }, [versions])

  function handleCompare(version: AgentPresetVersionReadMinimal) {
    setBaseVersionId(version.id)
    if (currentVersionId && currentVersionId !== version.id) {
      setCompareToId(currentVersionId)
    } else {
      const otherVersion = versions?.find(
        (candidate) => candidate.id !== version.id
      )
      setCompareToId(otherVersion?.id ?? null)
    }
    setView("compare")
  }

  async function handleRestore(versionId: string) {
    if (!presetId) {
      return
    }
    await restoreAgentPresetVersion({ presetId, versionId })
  }

  if (!preset) {
    return (
      <div className="flex h-full items-center justify-center px-4">
        <Empty>
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <History />
            </EmptyMedia>
            <EmptyTitle>Versions</EmptyTitle>
            <EmptyDescription>
              Save the agent to start tracking versions.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      </div>
    )
  }

  return (
    <div className="flex h-full flex-col">
      <div className="flex h-10 items-center justify-between border-b px-3">
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {view === "compare" ? (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 px-2 text-xs"
              onClick={() => setView("history")}
            >
              <ArrowLeft className="mr-2 size-3.5" />
              Back
            </Button>
          ) : (
            <>
              <History className="size-3.5" />
              <span>Preset history</span>
            </>
          )}
        </div>
        {currentVersionId ? (
          <Badge variant="secondary">
            Current {getVersionName(versionById.get(currentVersionId))}
          </Badge>
        ) : null}
      </div>
      <ScrollArea className="flex-1">
        {view === "history" ? (
          <VersionsHistoryView
            versions={versions}
            versionsIsLoading={versionsIsLoading}
            versionsError={versionsError}
            currentVersionId={currentVersionId}
            restorePending={restoreAgentPresetVersionIsPending}
            onCompare={handleCompare}
            onRestore={handleRestore}
          />
        ) : (
          <VersionsCompareView
            versions={versions ?? []}
            diff={diff}
            diffIsLoading={diffIsLoading}
            baseVersionId={baseVersionId}
            compareToId={compareToId}
            onBaseVersionChange={setBaseVersionId}
            onCompareToChange={setCompareToId}
          />
        )}
      </ScrollArea>
    </div>
  )
}

interface VersionsHistoryViewProps {
  versions?: AgentPresetVersionReadMinimal[]
  versionsIsLoading: boolean
  versionsError: unknown
  currentVersionId: string | null
  restorePending: boolean
  onCompare: (version: AgentPresetVersionReadMinimal) => void
  onRestore: (versionId: string) => Promise<void>
}

function VersionsHistoryView({
  versions,
  versionsIsLoading,
  versionsError,
  currentVersionId,
  restorePending,
  onCompare,
  onRestore,
}: VersionsHistoryViewProps) {
  if (versionsIsLoading) {
    return (
      <div className="flex items-center justify-center py-10">
        <CenteredSpinner />
      </div>
    )
  }

  if (versionsError) {
    return (
      <div className="px-4 py-6 text-sm text-destructive">
        Failed to load preset versions.
      </div>
    )
  }

  if (!versions?.length) {
    return (
      <div className="px-4 py-6 text-sm text-muted-foreground">
        No versions found yet.
      </div>
    )
  }

  return (
    <TooltipProvider>
      <div className="flex flex-col">
        {versions.map((version, index) => {
          const isCurrent = version.id === currentVersionId
          return (
            <div key={version.id}>
              <div className="flex items-center gap-3 px-4 py-3">
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">{`v${version.version}`}</span>
                    {isCurrent ? (
                      <Badge variant="secondary">Current</Badge>
                    ) : null}
                  </div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {getRelativeTime(new Date(version.created_at))}
                  </div>
                </div>
                <div className="flex items-center gap-2">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        type="button"
                        variant="ghost"
                        size="icon"
                        className="size-8"
                        onClick={() => onCompare(version)}
                        aria-label="Compare"
                      >
                        <GitCompareArrows className="size-3.5" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Compare</TooltipContent>
                  </Tooltip>
                  {!isCurrent ? (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon"
                          className="size-8"
                          disabled={restorePending}
                          onClick={() => void onRestore(version.id)}
                          aria-label="Restore"
                        >
                          <RotateCcw className="size-3.5" />
                        </Button>
                      </TooltipTrigger>
                      <TooltipContent>Restore</TooltipContent>
                    </Tooltip>
                  ) : null}
                </div>
              </div>
              {index < versions.length - 1 ? <Separator /> : null}
            </div>
          )
        })}
      </div>
    </TooltipProvider>
  )
}

interface VersionsCompareViewProps {
  versions: AgentPresetVersionReadMinimal[]
  diff?: AgentPresetVersionDiff
  diffIsLoading: boolean
  baseVersionId: string | null
  compareToId: string | null
  onBaseVersionChange: (versionId: string | null) => void
  onCompareToChange: (versionId: string | null) => void
}

function VersionsCompareView({
  versions,
  diff,
  diffIsLoading,
  baseVersionId,
  compareToId,
  onBaseVersionChange,
  onCompareToChange,
}: VersionsCompareViewProps) {
  const scalarChanges = diff?.scalar_changes ?? []
  const listChanges = diff?.list_changes ?? []
  const toolApprovalChanges = diff?.tool_approval_changes ?? []
  const totalChanges = diff?.total_changes ?? 0
  const baseVersion = versions.find((version) => version.id === baseVersionId)
  const compareVersion = versions.find((version) => version.id === compareToId)

  return (
    <div className="space-y-5 px-4 py-4">
      <div className="grid gap-3 md:grid-cols-2">
        <div className="space-y-2">
          <label className="text-xs font-medium text-muted-foreground">
            Base version
          </label>
          <Select
            value={baseVersionId ?? undefined}
            onValueChange={onBaseVersionChange}
          >
            <SelectTrigger>
              <SelectValue placeholder="Select version" />
            </SelectTrigger>
            <SelectContent>
              {versions.map((version) => (
                <SelectItem key={version.id} value={version.id}>
                  {`v${version.version}`}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="space-y-2">
          <label className="text-xs font-medium text-muted-foreground">
            Compare to
          </label>
          <Select
            value={compareToId ?? undefined}
            onValueChange={onCompareToChange}
          >
            <SelectTrigger>
              <SelectValue placeholder="Select version" />
            </SelectTrigger>
            <SelectContent>
              {versions.map((version) => (
                <SelectItem key={version.id} value={version.id}>
                  {`v${version.version}`}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {diffIsLoading ? (
        <div className="flex items-center justify-center py-10">
          <CenteredSpinner />
        </div>
      ) : null}

      {diff && !diffIsLoading ? (
        <div className="space-y-5">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <Badge variant="secondary">
              {`Viewing ${totalChanges} change${totalChanges === 1 ? "" : "s"} from ${getVersionName(baseVersion)} to ${getVersionName(compareVersion)}`}
            </Badge>
          </div>

          <div className="space-y-2">
            <div className="text-sm font-medium">Prompt changes</div>
            <AgentPresetPromptDiff
              baseLabel={getVersionName(baseVersion)}
              basePrompt={diff.base_instructions}
              compareLabel={getVersionName(compareVersion)}
              comparePrompt={diff.compare_instructions}
            />
          </div>

          {scalarChanges.length > 0 ? (
            <div className="space-y-3">
              <h4 className="text-sm font-medium">Configuration changes</h4>
              <div className="rounded-md border">
                {scalarChanges.map((change, index) => (
                  <div key={change.field}>
                    <div className="grid gap-3 px-3 py-3 text-xs md:grid-cols-[140px_1fr_1fr]">
                      <div className="font-medium text-foreground">
                        {change.field}
                      </div>
                      <pre className="whitespace-pre-wrap text-muted-foreground">
                        {stringifyDiffValue(change.old_value)}
                      </pre>
                      <pre className="whitespace-pre-wrap text-foreground">
                        {stringifyDiffValue(change.new_value)}
                      </pre>
                    </div>
                    {index < scalarChanges.length - 1 ? <Separator /> : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {listChanges.length > 0 ? (
            <div className="space-y-3">
              <h4 className="text-sm font-medium">
                Tool and integration changes
              </h4>
              <div className="space-y-3">
                {listChanges.map((change) => {
                  const added = change.added ?? []
                  const removed = change.removed ?? []
                  return (
                    <div key={change.field} className="rounded-md border p-3">
                      <div className="text-xs font-medium text-foreground">
                        {change.field}
                      </div>
                      <div className="mt-3 grid gap-3 text-xs md:grid-cols-2">
                        <div>
                          <div className="mb-2 text-muted-foreground">
                            Added
                          </div>
                          <ul className="space-y-1">
                            {added.length > 0 ? (
                              added.map((value) => (
                                <li key={value}>+ {value}</li>
                              ))
                            ) : (
                              <li className="text-muted-foreground">None</li>
                            )}
                          </ul>
                        </div>
                        <div>
                          <div className="mb-2 text-muted-foreground">
                            Removed
                          </div>
                          <ul className="space-y-1">
                            {removed.length > 0 ? (
                              removed.map((value) => (
                                <li key={value}>- {value}</li>
                              ))
                            ) : (
                              <li className="text-muted-foreground">None</li>
                            )}
                          </ul>
                        </div>
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          ) : null}

          {toolApprovalChanges.length > 0 ? (
            <div className="space-y-3">
              <h4 className="text-sm font-medium">Approval changes</h4>
              <div className="rounded-md border">
                {toolApprovalChanges.map((change, index) => (
                  <div key={change.tool}>
                    <div className="grid gap-3 px-3 py-3 text-xs md:grid-cols-[1fr_120px_120px]">
                      <div className="font-medium text-foreground">
                        {change.tool}
                      </div>
                      <div className="text-muted-foreground">
                        {stringifyDiffValue(change.old_value)}
                      </div>
                      <div>{stringifyDiffValue(change.new_value)}</div>
                    </div>
                    {index < toolApprovalChanges.length - 1 ? (
                      <Separator />
                    ) : null}
                  </div>
                ))}
              </div>
            </div>
          ) : null}

          {totalChanges === 0 ? (
            <div className="text-sm text-muted-foreground">
              No changes between these versions.
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}
