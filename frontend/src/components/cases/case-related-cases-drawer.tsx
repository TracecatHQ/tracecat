"use client"

import { ExternalLink } from "lucide-react"
import {
  type MouseEvent,
  type ReactNode,
  useEffect,
  useRef,
  useState,
} from "react"
import type { CaseReadMinimal } from "@/client"
import { CaseBadge, CaseColumnBadge } from "@/components/cases/case-badge"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { CaseItem } from "@/components/cases/case-item"
import { CaseValueDrawer } from "@/components/cases/case-value-drawer"
import { JsonViewWithControls } from "@/components/json-viewer"
import { Spinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Skeleton } from "@/components/ui/skeleton"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useLinkedCases } from "@/hooks/use-linked-cases"
import { useWorkspaceMembers } from "@/hooks/use-workspace"
import { getDisplayName } from "@/lib/auth"
import {
  formatCaseFieldDisplayLabel,
  isCustomFieldValueEmpty,
} from "@/lib/case-field-display"
import {
  useCaseDropdownDefinitions,
  useCaseTagCatalog,
  useGetCase,
} from "@/lib/hooks"

/** The linked row whose cases the drawer lists. */
export interface RelatedCasesTarget {
  tableId: string
  tableName: string | null
  rowId: string
  /** The row's column values, keyed by column name. */
  rowData: Record<string, unknown>
}

/** Props for {@link CaseRelatedCasesDrawer}. */
export interface CaseRelatedCasesDrawerProps {
  /** The row to list cases for; `null` keeps the drawer closed. */
  target: RelatedCasesTarget | null
  onClose: () => void
  /** The case being viewed, left out of the list. */
  caseId: string
  workspaceId: string
}

const BADGE_CLASS = "h-5 shrink-0 px-1.5 py-0 text-[10px]"

function caseHref(workspaceId: string, caseId: string): string {
  return `/workspaces/${workspaceId}/cases/${caseId}`
}

function openCaseInNewTab(workspaceId: string, caseId: string): void {
  window.open(caseHref(workspaceId, caseId), "_blank", "noopener,noreferrer")
}

/**
 * Every other case that links a row, in the case page's wide left drawer,
 * under the row's contents. Rows look like the cases list; hovering one shows
 * all of its properties and custom fields, and clicking pins that card open.
 */
export function CaseRelatedCasesDrawer({
  target,
  onClose,
  caseId,
  workspaceId,
}: CaseRelatedCasesDrawerProps) {
  return (
    <CaseValueDrawer
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose()
      }}
      title="Related cases"
      description="Cases related to selected linked row"
      size="wide"
    >
      {target && (
        <RelatedCasesList
          key={`${target.tableId}:${target.rowId}`}
          target={target}
          caseId={caseId}
          workspaceId={workspaceId}
        />
      )}
    </CaseValueDrawer>
  )
}

function RelatedCasesList({
  target,
  caseId,
  workspaceId,
}: {
  target: RelatedCasesTarget
  caseId: string
  workspaceId: string
}) {
  const {
    linkedCases,
    linkedCasesIsLoading,
    linkedCasesError,
    hasNextPage,
    isFetchingNextPage,
    fetchNextPage,
  } = useLinkedCases({
    workspaceId,
    tableId: target.tableId,
    rowId: target.rowId,
    excludeCaseId: caseId,
  })
  const { hasEntitlement } = useEntitlements()
  const caseAddonsEnabled = hasEntitlement("case_addons")
  const { members } = useWorkspaceMembers(workspaceId)
  const { caseTags } = useCaseTagCatalog(workspaceId)
  const { dropdownDefinitions } = useCaseDropdownDefinitions(
    workspaceId,
    caseAddonsEnabled
  )
  // One card shows at a time: the hovered case's, else the pinned one's.
  const [hoveredCaseId, setHoveredCaseId] = useState<string | null>(null)
  const [pinnedCaseId, setPinnedCaseId] = useState<string | null>(null)

  // Leaving the window (e.g. for a case opened in a new tab) skips the row's
  // pointerleave, which would otherwise leave its card open.
  useEffect(() => {
    const clearHover = () => setHoveredCaseId(null)
    window.addEventListener("blur", clearHover)
    window.addEventListener("focus", clearHover)
    return () => {
      window.removeEventListener("blur", clearHover)
      window.removeEventListener("focus", clearHover)
    }
  }, [])

  let body: ReactNode
  if (linkedCasesIsLoading) {
    body = (
      <div className="space-y-2 p-4">
        {[...Array(4)].map((_, index) => (
          <Skeleton key={index} className="h-8 rounded-md" />
        ))}
      </div>
    )
  } else if (linkedCasesError) {
    body = (
      <p className="p-4 text-sm text-muted-foreground">
        Failed to load related cases.
      </p>
    )
  } else if (linkedCases.length === 0) {
    body = (
      <p className="p-4 text-sm text-muted-foreground">
        No other cases link this row.
      </p>
    )
  } else {
    body = (
      <div className="divide-y">
        {linkedCases.map((caseData) => (
          <RelatedCaseRow
            key={caseData.id}
            caseData={caseData}
            workspaceId={workspaceId}
            cardOpen={
              hoveredCaseId === caseData.id ||
              (hoveredCaseId === null && pinnedCaseId === caseData.id)
            }
            onHoverChange={(hovered) =>
              setHoveredCaseId((current) =>
                hovered ? caseData.id : current === caseData.id ? null : current
              )
            }
            onTogglePin={() =>
              setPinnedCaseId((current) =>
                current === caseData.id ? null : caseData.id
              )
            }
            onUnpin={() =>
              setPinnedCaseId((current) =>
                current === caseData.id ? null : current
              )
            }
            tags={caseTags}
            members={members}
            dropdownDefinitions={
              caseAddonsEnabled ? dropdownDefinitions : undefined
            }
          />
        ))}
        {hasNextPage && (
          <div className="flex justify-center p-3">
            <Button
              variant="ghost"
              size="sm"
              className="h-7 text-xs text-muted-foreground"
              disabled={isFetchingNextPage}
              onClick={() => fetchNextPage()}
            >
              {isFetchingNextPage && <Spinner className="mr-1 size-3" />}
              Load more
            </Button>
          </div>
        )}
      </div>
    )
  }

  return (
    <div className="h-full overflow-y-auto">
      <section className="border-b px-4 py-3">
        <h3 className="mb-2 text-xs font-medium text-muted-foreground">
          Linked row{target.tableName ? ` in ${target.tableName}` : ""}
        </h3>
        <div className="max-h-72 overflow-y-auto">
          <JsonViewWithControls
            src={target.rowData}
            defaultExpanded
            copyMode="jsonpath-and-payload"
          />
        </div>
      </section>
      <h3 className="px-4 pb-1 pt-3 text-xs font-medium text-muted-foreground">
        Cases
      </h3>
      {body}
    </div>
  )
}

function RelatedCaseRow({
  caseData,
  workspaceId,
  cardOpen,
  onHoverChange,
  onTogglePin,
  onUnpin,
  tags,
  members,
  dropdownDefinitions,
}: {
  caseData: CaseReadMinimal
  workspaceId: string
  cardOpen: boolean
  onHoverChange: (hovered: boolean) => void
  onTogglePin: () => void
  onUnpin: () => void
} & Pick<
  React.ComponentProps<typeof CaseItem>,
  "tags" | "members" | "dropdownDefinitions"
>) {
  const triggerRef = useRef<HTMLDivElement>(null)

  // Cmd/Ctrl-click and middle-click open the case instead of pinning its card.
  // The row gives up focus first: the browser restores it when the tab comes
  // back, and a focused trigger would reopen the card.
  function openInNewTab() {
    const focused = document.activeElement
    if (
      focused instanceof HTMLElement &&
      triggerRef.current?.contains(focused)
    ) {
      focused.blur()
    }
    onHoverChange(false)
    openCaseInNewTab(workspaceId, caseData.id)
  }

  function handleClickCapture(event: MouseEvent) {
    if (!event.metaKey && !event.ctrlKey) return
    event.preventDefault()
    event.stopPropagation()
    openInNewTab()
  }

  function handleAuxClick(event: MouseEvent) {
    if (event.button !== 1) return
    event.preventDefault()
    openInNewTab()
  }

  return (
    <HoverCard
      open={cardOpen}
      onOpenChange={onHoverChange}
      openDelay={300}
      closeDelay={100}
    >
      <HoverCardTrigger asChild>
        {/* Cancels CaseItem's negative left margin, which the cases list pads for. */}
        <div
          ref={triggerRef}
          className="pl-[18px]"
          onClickCapture={handleClickCapture}
          onAuxClick={handleAuxClick}
        >
          <CaseItem
            caseData={caseData}
            isSelected={false}
            selectable={false}
            onClick={onTogglePin}
            tags={tags}
            members={members}
            dropdownDefinitions={dropdownDefinitions}
          />
        </div>
      </HoverCardTrigger>
      <HoverCardContent
        side="right"
        align="start"
        sideOffset={8}
        collisionPadding={8}
        className="max-h-[min(80vh,40rem)] w-96 overflow-y-auto p-0"
        onPointerDownOutside={(event) => {
          // A click on this row toggles the pin itself, and modifier or
          // middle clicks only open a case in a new tab.
          const { originalEvent } = event.detail
          if (
            originalEvent.ctrlKey ||
            originalEvent.metaKey ||
            originalEvent.button === 1 ||
            triggerRef.current?.contains(event.target as Node)
          ) {
            return
          }
          onUnpin()
        }}
        onEscapeKeyDown={onUnpin}
      >
        <RelatedCaseProperties caseData={caseData} workspaceId={workspaceId} />
      </HoverCardContent>
    </HoverCard>
  )
}

function PropertyRow({
  label,
  children,
}: {
  label: string
  children: ReactNode
}) {
  return (
    <div className="grid grid-cols-[8rem_minmax(0,1fr)] items-center gap-2 py-1">
      <span className="truncate text-xs text-muted-foreground">{label}</span>
      <div className="flex min-w-0 flex-wrap items-center gap-1 text-xs">
        {children}
      </div>
    </div>
  )
}

function EmptyValue() {
  return <span className="text-muted-foreground">—</span>
}

function formatTimestamp(timestamp: string): string {
  const date = new Date(timestamp)
  return Number.isNaN(date.getTime()) ? timestamp : date.toLocaleString()
}

/**
 * A related case's full property sheet. The list row already holds the core
 * properties and dropdowns; custom fields come from the case itself, fetched
 * when the card first opens.
 */
function RelatedCaseProperties({
  caseData,
  workspaceId,
}: {
  caseData: CaseReadMinimal
  workspaceId: string
}) {
  const {
    caseData: detail,
    caseDataIsLoading,
    caseDataError,
  } = useGetCase({
    caseId: caseData.id,
    workspaceId,
  })
  const status = STATUSES[caseData.status]
  const priority = PRIORITIES[caseData.priority]
  const severity = SEVERITIES[caseData.severity]
  const caseTags = caseData.tags ?? []
  const customFields = detail?.fields.filter((field) => !field.reserved) ?? []

  return (
    <div className="flex flex-col">
      <div className="space-y-1 border-b px-4 py-3">
        <div className="flex items-center justify-between gap-2">
          <span className="text-xs font-medium text-muted-foreground">
            {caseData.short_id}
          </span>
          <Button
            asChild
            variant="ghost"
            size="sm"
            className="h-6 gap-1 px-2 text-xs"
          >
            <a
              href={caseHref(workspaceId, caseData.id)}
              target="_blank"
              rel="noopener noreferrer"
            >
              Open case
              <ExternalLink className="size-3" />
            </a>
          </Button>
        </div>
        <p className="line-clamp-2 text-sm font-medium">{caseData.summary}</p>
      </div>
      <div className="px-4 py-2">
        <PropertyRow label="Status">
          {status ? (
            <CaseBadge {...status} className={BADGE_CLASS} />
          ) : (
            caseData.status
          )}
        </PropertyRow>
        <PropertyRow label="Priority">
          {priority ? (
            <CaseBadge {...priority} className={BADGE_CLASS} />
          ) : (
            caseData.priority
          )}
        </PropertyRow>
        <PropertyRow label="Severity">
          {severity ? (
            <CaseBadge {...severity} className={BADGE_CLASS} />
          ) : (
            caseData.severity
          )}
        </PropertyRow>
        <PropertyRow label="Assignee">
          {caseData.assignee ? (
            <span className="truncate">
              {getDisplayName(caseData.assignee)}
            </span>
          ) : (
            <EmptyValue />
          )}
        </PropertyRow>
        <PropertyRow label="Tags">
          {caseTags.length > 0 ? (
            caseTags.map((tag) => (
              <CaseColumnBadge
                key={tag.id}
                label={tag.name}
                color={tag.color}
                className={BADGE_CLASS}
              />
            ))
          ) : (
            <EmptyValue />
          )}
        </PropertyRow>
        <PropertyRow label="Tasks">
          <span className="tabular-nums">
            {caseData.num_tasks_completed}/{caseData.num_tasks_total}
          </span>
        </PropertyRow>
        <PropertyRow label="Created">
          {formatTimestamp(caseData.created_at)}
        </PropertyRow>
        <PropertyRow label="Updated">
          {formatTimestamp(caseData.updated_at)}
        </PropertyRow>
      </div>
      {caseData.dropdown_values.length > 0 && (
        <div className="border-t px-4 py-2">
          {caseData.dropdown_values.map((value) => (
            <PropertyRow
              key={value.definition_id}
              label={value.definition_name || value.definition_ref}
            >
              {value.option_label ? (
                <CaseColumnBadge
                  label={value.option_label}
                  iconName={value.option_icon_name}
                  color={value.option_color}
                  className={BADGE_CLASS}
                />
              ) : (
                <EmptyValue />
              )}
            </PropertyRow>
          ))}
        </div>
      )}
      <div className="border-t px-4 py-2">
        {caseDataIsLoading ? (
          <div className="space-y-2 py-1">
            <Skeleton className="h-4 rounded" />
            <Skeleton className="h-4 rounded" />
          </div>
        ) : caseDataError ? (
          <p className="py-1 text-xs text-muted-foreground">
            Failed to load custom fields.
          </p>
        ) : customFields.length === 0 ? (
          <p className="py-1 text-xs text-muted-foreground">
            No custom fields.
          </p>
        ) : (
          customFields.map((field) => (
            <PropertyRow key={field.id} label={field.display_name}>
              {isCustomFieldValueEmpty(field.value) ? (
                <EmptyValue />
              ) : (
                <span className="break-words">
                  {formatCaseFieldDisplayLabel(field.value, field.type)}
                </span>
              )}
            </PropertyRow>
          ))
        )}
      </div>
    </div>
  )
}
