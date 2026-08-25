"use client"

import { Loader2 } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type { CaseVersionField, CaseVersionReadMinimal } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { InlineDiffView } from "@/components/diff/inline-diff-view"
import {
  DropdownMenuItem,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
} from "@/components/ui/dropdown-menu"
import { ScrollArea } from "@/components/ui/scroll-area"
import type {
  VersionedDocumentDescriptor,
  VersionHistoryEntry,
} from "@/components/version-history/types"
import { VersionHistoryMenu } from "@/components/version-history/version-history-menu"
import {
  useCaseVersionComparison,
  useCaseVersions,
  useRestoreCaseVersion,
} from "@/hooks/use-case-versions"
import { getDisplayName } from "@/lib/auth"

type CaseVersionFilter = "all" | CaseVersionField

const CASE_VERSION_FILTERS: ReadonlyArray<{
  value: CaseVersionFilter
  label: string
}> = [
  { value: "all", label: "All" },
  { value: "summary", label: "Title" },
  { value: "description", label: "Description" },
]

/** Props for the case-specific version-history adapter. */
export interface CaseVersionHistoryProps {
  workspaceId: string
  caseId: string
  /** Stable case label used by the shared history shell. */
  caseLabel: string
}

function fieldLabel(field: CaseVersionField): "Title" | "Description" {
  return field === "summary" ? "Title" : "Description"
}

function versionLabel(
  version: Pick<CaseVersionReadMinimal, "field" | "version">
): string {
  return `${fieldLabel(version.field)} v${version.version}`
}

function versionDescription(version: CaseVersionReadMinimal): string {
  const actor = version.actor ? getDisplayName(version.actor) : "System"
  return `${actor} · ${new Date(version.created_at).toLocaleString()}`
}

function loadMoreLabel({
  isFetching,
  hasError,
}: {
  isFetching: boolean
  hasError: boolean
}): string {
  if (isFetching) {
    return "Loading more…"
  }
  if (hasError) {
    return "Couldn't load more. Retry"
  }
  return "Load more"
}

function CaseVersionDiffContent({
  workspaceId,
  caseId,
  versionId,
}: {
  workspaceId: string
  caseId: string
  versionId: string
}) {
  const { comparison, comparisonIsLoading, comparisonError } =
    useCaseVersionComparison({ workspaceId, caseId, versionId })

  if (comparisonIsLoading) {
    return (
      <div className="flex min-h-56 items-center justify-center gap-2 rounded-md border text-sm text-muted-foreground">
        <Loader2 className="size-4 animate-spin" />
        Loading comparison…
      </div>
    )
  }

  if (comparisonError || !comparison) {
    return (
      <div className="flex min-h-56 items-center justify-center rounded-md border px-4 text-sm text-muted-foreground">
        Couldn't load this version comparison.
      </div>
    )
  }

  const selectedLabel = versionLabel(comparison.selected)
  const predecessor = comparison.predecessor
  const isBaseline = predecessor == null
  const path =
    comparison.selected.field === "summary" ? "title.txt" : "description.txt"

  return (
    <div className="flex h-[min(32rem,50vh)] min-h-0 flex-col overflow-hidden rounded-md border">
      <div className="flex items-center justify-between gap-3 border-b px-4 py-2 text-xs">
        <span className="font-medium">{selectedLabel}</span>
        <span className="text-muted-foreground">
          {isBaseline
            ? "Baseline"
            : `${versionLabel(predecessor)} → ${selectedLabel}`}
        </span>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-4 p-4">
          {isBaseline ? (
            <p className="text-sm text-muted-foreground">
              This is the first{" "}
              {fieldLabel(comparison.selected.field).toLowerCase()} version and
              has no predecessor.
            </p>
          ) : null}
          <InlineDiffView
            path={path}
            oldValue={predecessor?.content ?? ""}
            newValue={comparison.selected.content}
            mode="prose"
          />
        </div>
      </ScrollArea>
    </div>
  )
}

/**
 * Case version history adapter: owns field filters, pagination, predecessor
 * comparisons, restore behavior, and case-specific copy while reusing the
 * document-agnostic history shell.
 */
export function CaseVersionHistory({
  workspaceId,
  caseId,
  caseLabel,
}: CaseVersionHistoryProps) {
  const canRestore = useScopeCheck("case:update") === true
  const [filter, setFilter] = useState<CaseVersionFilter>("all")
  const field = filter === "all" ? null : filter
  const {
    versions,
    versionsIsLoading,
    versionsError,
    hasNextPage,
    isFetchingNextPage,
    isFetchNextPageError,
    fetchNextPage,
  } = useCaseVersions({ workspaceId, caseId, field })
  const { restoreCaseVersion } = useRestoreCaseVersion({ workspaceId, caseId })

  useEffect(() => setFilter("all"), [caseId])

  const versionById = useMemo(
    () => new Map(versions.map((version) => [version.id, version] as const)),
    [versions]
  )
  const entries = useMemo<VersionHistoryEntry[]>(
    () =>
      versions.map((version) => ({
        id: version.id,
        label: versionLabel(version),
        description: versionDescription(version),
        createdAt: version.created_at,
        isCurrent: version.is_latest,
      })),
    [versions]
  )
  const documentDescriptor = useMemo<VersionedDocumentDescriptor>(
    () => ({
      entityLabel: "case",
      name: caseLabel,
      currentVersionId: null,
    }),
    [caseLabel]
  )

  function comparisonDescription(entry: VersionHistoryEntry): string {
    const version = versionById.get(entry.id)
    if (!version || version.version === 1) {
      return `${entry.label} is the baseline and has no predecessor.`
    }
    return `Compare ${entry.label} with ${fieldLabel(version.field)} v${version.version - 1}.`
  }

  function restoreDescription(entry: VersionHistoryEntry): string {
    const version = versionById.get(entry.id)
    const label = version ? fieldLabel(version.field).toLowerCase() : "field"
    return `Restoring replaces the current persisted ${label} and any unsaved ${label} draft.`
  }

  const listFooter =
    hasNextPage || isFetchingNextPage || isFetchNextPageError ? (
      <DropdownMenuItem
        className="justify-center px-3 py-2"
        disabled={isFetchingNextPage}
        onSelect={(event) => {
          event.preventDefault()
          if (!isFetchingNextPage) {
            void fetchNextPage()
          }
        }}
      >
        {isFetchingNextPage ? (
          <Loader2 className="mr-2 size-4 animate-spin" />
        ) : null}
        {loadMoreLabel({
          isFetching: isFetchingNextPage,
          hasError: isFetchNextPageError,
        })}
      </DropdownMenuItem>
    ) : null

  return (
    <VersionHistoryMenu
      document={documentDescriptor}
      entityLabel="case"
      versions={entries}
      isLoading={versionsIsLoading}
      loadError={Boolean(versionsError) && versions.length === 0}
      listControls={
        <DropdownMenuRadioGroup
          className="p-1"
          value={filter}
          onValueChange={(value) => setFilter(value as CaseVersionFilter)}
        >
          {CASE_VERSION_FILTERS.map((option) => (
            <DropdownMenuRadioItem
              key={option.value}
              value={option.value}
              onSelect={(event) => event.preventDefault()}
            >
              {option.label}
            </DropdownMenuRadioItem>
          ))}
        </DropdownMenuRadioGroup>
      }
      listFooter={listFooter}
      renderComparisonDescription={comparisonDescription}
      renderRestoreConfirmationDescription={restoreDescription}
      restoreDisabled={!canRestore}
      isRestoreDisabled={(entry) => entry.isCurrent === true}
      onRestore={async (versionId) => {
        await restoreCaseVersion({ versionId })
      }}
      renderVersionDiff={(versionId) => (
        <CaseVersionDiffContent
          workspaceId={workspaceId}
          caseId={caseId}
          versionId={versionId}
        />
      )}
    />
  )
}
