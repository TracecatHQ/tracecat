"use client"

import { useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useId, useMemo, useState } from "react"
import type {
  CaseDropdownDefinitionRead,
  CasePriority,
  CaseSeverity,
  CaseStatus,
  CaseUpdate,
} from "@/client"
import { CaseClosureDialog } from "@/components/cases/case-closure-dialog"
import { CommentSection } from "@/components/cases/case-comments-section"
import { CASE_PANEL_GROUP_LABEL_CLASS } from "@/components/cases/case-panel-common"
import { CasePanelContent } from "@/components/cases/case-panel-content"
import { CasePanelFieldsGroup } from "@/components/cases/case-panel-fields-group"
import {
  type AssigneeInfo,
  AssigneeSelect,
  CaseDropdownSelect,
  PrioritySelect,
  SeveritySelect,
  StatusSelect,
} from "@/components/cases/case-panel-selectors"
import { CasePanelSummary } from "@/components/cases/case-panel-summary"
import { CasePanelSwitcher } from "@/components/cases/case-panel-switcher"
import {
  CASE_PANELS,
  type CasePanelKey,
  casePanelPanelId,
  casePanelTabId,
  DEFAULT_CASE_PANEL,
  parseCasePanelKey,
} from "@/components/cases/case-panels"
import { CaseTagPicker } from "@/components/cases/case-tag-picker"
import { getCaseTaskProgress } from "@/components/cases/case-task-status"
import { CaseWorkflowTrigger } from "@/components/cases/case-workflow-trigger"
import { AlertNotification } from "@/components/notifications"
import { TagBadge } from "@/components/tag-badge"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Sidebar,
  SidebarContent,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
} from "@/components/ui/sidebar"
import { Skeleton } from "@/components/ui/skeleton"
import { useDigitShortcuts } from "@/hooks/use-digit-shortcuts"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useIsAtLeastWidth } from "@/hooks/use-is-at-least-width"
import { useWorkspaceMembers } from "@/hooks/use-workspace"
import {
  isCustomFieldValueEmpty,
  orderCustomFieldsForDisplay,
} from "@/lib/case-field-display"
import {
  useCaseDropdownDefinitions,
  useCaseDurationDefinitions,
  useCaseDurations,
  useCaseFields,
  useCaseTasks,
  useGetCase,
  useSetCaseDropdownValue,
  useUpdateCase,
} from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

/**
 * Minimum `.tc-case-panel` width (px) at which the details rail stays docked:
 * the 384px (24rem) rail plus a ~656px minimum usable case body. Held at 1040
 * rather than tracking the rail width down, so a narrowing row stacks slightly
 * earlier and hands the body its full width instead of squeezing the tabs.
 */
const CASE_DETAILS_DOCK_MIN_WIDTH = 1040

/**
 * Width of the docked details rail. Shared by the rail itself and by the
 * spacer that reserves the same width in the switcher band, so the band's
 * centered column stays the same box as the body's and the tabs keep landing
 * on the case title. A literal string constant rather than two inline classes:
 * Tailwind still sees the full class name in this file, and the two cannot
 * drift apart.
 */
const CASE_DETAILS_RAIL_WIDTH_CLASS = "w-[24rem]"

interface CasePanelViewProps {
  caseId: string
  embedded?: boolean
  initialTab?: string | null
  onTabChange?: (tab: string) => void
}

/** Full case details view: top switcher band, active panel, comments, and the properties rail. */
export function CasePanelView({
  caseId,
  embedded = false,
  initialTab,
  onTabChange,
}: CasePanelViewProps) {
  const workspaceId = useWorkspaceId()
  const { members } = useWorkspaceMembers(workspaceId)
  const router = useRouter()
  const searchParams = useSearchParams()
  const { hasEntitlement, isLoading: entitlementsIsLoading } = useEntitlements()
  const caseAddonsEnabled = hasEntitlement("case_addons")

  const { caseData, caseDataIsLoading, caseDataError } = useGetCase({
    caseId,
    workspaceId,
  })
  useCaseDurations({
    caseId,
    workspaceId,
    enabled: caseAddonsEnabled,
  })
  useCaseDurationDefinitions(workspaceId, caseAddonsEnabled)
  const { updateCase } = useUpdateCase({
    workspaceId,
    caseId,
  })
  const { dropdownDefinitions } = useCaseDropdownDefinitions(
    workspaceId,
    caseAddonsEnabled
  )
  const setDropdownValue = useSetCaseDropdownValue(workspaceId)
  const { caseFields: caseFieldDefinitions } = useCaseFields(workspaceId)
  const [closureDialog, setClosureDialog] = useState<{
    open: boolean
    targetStatus: CaseStatus
  } | null>(null)
  const customFields = useMemo(
    () => (caseData?.fields ?? []).filter((field) => !field.reserved),
    [caseData?.fields]
  )
  const [showAllCustomFields, setShowAllCustomFields] = useState(false)
  const [embeddedPanel, setEmbeddedPanel] = useState<CasePanelKey>(
    () => parseCasePanelKey(initialTab) ?? DEFAULT_CASE_PANEL
  )
  const visibleCustomFields = useMemo(
    () => orderCustomFieldsForDisplay(customFields, showAllCustomFields),
    [customFields, showAllCustomFields]
  )
  // Active panel from the URL `?tab=` param (or embedded state), defaulting
  // to the description. Entitlement gating has three surfaces — the hidden
  // switcher button, the no-op digit shortcut, and `?tab=tasks` deep links —
  // so a hidden requested panel resolves to the default rather than landing
  // on an empty panel with no button to leave it. While entitlements load,
  // the switcher reserves an invisible Tasks slot so the right-aligned row
  // never reflows when the answer arrives.
  const routePanel =
    parseCasePanelKey(searchParams?.get("tab")) ?? DEFAULT_CASE_PANEL
  const requestedPanel = embedded ? embeddedPanel : routePanel
  const hiddenPanelKeys = useMemo<readonly CasePanelKey[]>(
    () => (caseAddonsEnabled ? [] : ["tasks"]),
    [caseAddonsEnabled]
  )
  const pendingPanelKeys = useMemo<readonly CasePanelKey[]>(
    () => (!caseAddonsEnabled && entitlementsIsLoading ? ["tasks"] : []),
    [caseAddonsEnabled, entitlementsIsLoading]
  )
  const activePanel = hiddenPanelKeys.includes(requestedPanel)
    ? DEFAULT_CASE_PANEL
    : requestedPanel
  const panelIdPrefix = useId()

  // The ring's query lives in the view, not the Tasks panel: the switcher
  // must show progress while the panel is unmounted. React Query dedupes on
  // the shared ["case-tasks", ...] key, so mounting the panel adds no second
  // request and its mutations refresh the ring for free. `enabled` keeps the
  // gated endpoint quiet for orgs without `case_addons`.
  const { caseTasks } = useCaseTasks({
    caseId,
    workspaceId,
    enabled: caseAddonsEnabled,
  })
  const taskProgress = useMemo(
    () => getCaseTaskProgress(caseTasks),
    [caseTasks]
  )

  // Measure the container, not the viewport: the chat sidebar
  // (`ResizableSidebar`, 450px default) is a sibling flex child, so with chat
  // open the case row is far narrower than the window and a viewport media
  // query would wrongly keep the rail docked.
  // A callback ref, not `useRef`: the measured node renders below the loading
  // and error early returns, so a ref object would still be null on the first
  // effect run and the observer would never attach.
  const [rootNode, setRootNode] = useState<HTMLDivElement | null>(null)
  const canDock = useIsAtLeastWidth(rootNode, CASE_DETAILS_DOCK_MIN_WIDTH)
  const showDockedDetails = !embedded && canDock
  const showInlineDetails = !embedded && !canDock

  useEffect(() => {
    if (!embedded) {
      return
    }
    const nextPanel = parseCasePanelKey(initialTab) ?? DEFAULT_CASE_PANEL
    if (nextPanel !== embeddedPanel) {
      setEmbeddedPanel(nextPanel)
    }
  }, [caseId, embedded, embeddedPanel, initialTab])

  const handlePanelChange = useCallback(
    (panel: CasePanelKey) => {
      if (embedded) {
        setEmbeddedPanel(panel)
        onTabChange?.(panel)
        return
      }
      // `replace`, not `push`, so Back leaves the page instead of walking
      // panel history; `scroll: false` keeps the body's scroll position.
      // Preserve the other query params rather than dropping them.
      const params = new URLSearchParams(searchParams?.toString() ?? "")
      params.set("tab", panel)
      router.replace(
        `/workspaces/${workspaceId}/cases/${caseId}?${params.toString()}`,
        { scroll: false }
      )
    },
    [embedded, router, searchParams, workspaceId, caseId, onTabChange]
  )

  const handleDigitShortcut = useCallback(
    (digit: number) => {
      const definition = CASE_PANELS.find((panel) => panel.shortcut === digit)
      // Digits never renumber: a hidden panel's digit stays a no-op.
      if (!definition || hiddenPanelKeys.includes(definition.key)) {
        return
      }
      handlePanelChange(definition.key)
    },
    [handlePanelChange, hiddenPanelKeys]
  )

  // Route-level instance only: a case route and a case artifact in the chat
  // panel can be mounted at once, and two window listeners would switch both
  // panels on one keypress.
  useDigitShortcuts({
    count: CASE_PANELS.length,
    onDigit: handleDigitShortcut,
    enabled: !embedded,
  })

  if (caseDataIsLoading) {
    return (
      <div className="flex h-full flex-col space-y-4 p-4">
        <div className="flex items-center justify-between border-b p-4">
          <div className="flex items-center space-x-4">
            <Skeleton className="h-4 w-16" />
            <div className="flex items-center space-x-2">
              <Skeleton className="h-3 w-32" />
              <Skeleton className="h-3 w-32" />
            </div>
          </div>
        </div>
        <Skeleton className="h-6 w-full" />
        <Skeleton className="h-[200px] w-full" />
        <div className="flex space-x-4">
          <Skeleton className="h-6 w-20" />
          <Skeleton className="h-6 w-20" />
          <Skeleton className="h-6 w-20" />
        </div>
      </div>
    )
  }
  if (caseDataError || !caseData) {
    return (
      <AlertNotification
        level="error"
        message={caseDataError?.message ?? "Error occurred loading case data"}
      />
    )
  }

  const handleStatusChange = async (newStatus: CaseStatus) => {
    if (
      caseAddonsEnabled &&
      (newStatus === "closed" || newStatus === "resolved")
    ) {
      const reqFields =
        caseFieldDefinitions?.filter(
          (f) => !f.reserved && f.required_on_closure
        ) ?? []
      const reqDropdowns =
        dropdownDefinitions?.filter((d) => d.required_on_closure) ?? []

      if (reqFields.length > 0 || reqDropdowns.length > 0) {
        // Check if any required field/dropdown is empty on the current case
        const hasEmptyField = reqFields.some((f) => {
          const field = caseData.fields.find((cf) => cf.id === f.id)
          return isCustomFieldValueEmpty(field?.value)
        })
        const hasEmptyDropdown = reqDropdowns.some((d) => {
          const dv = caseData.dropdown_values.find(
            (v) => v.definition_id === d.id
          )
          return !dv?.option_id
        })

        if (hasEmptyField || hasEmptyDropdown) {
          setClosureDialog({ open: true, targetStatus: newStatus })
          return
        }
      }
    }
    await updateCase({ status: newStatus })
  }

  const handlePriorityChange = async (newPriority: CasePriority) => {
    const params = {
      priority: newPriority,
    }
    await updateCase(params)
  }

  const handleSeverityChange = async (newSeverity: CaseSeverity) => {
    const params = {
      severity: newSeverity,
    }
    await updateCase(params)
  }

  const handleAssigneeChange = async (newAssignee?: AssigneeInfo | null) => {
    const params: Partial<CaseUpdate> = {
      assignee_id: newAssignee?.id || null,
    }
    await updateCase(params)
  }

  const panelFieldRowClassName = cn(
    "group -mx-2 flex h-7 w-full min-w-0 max-w-full cursor-pointer items-center gap-2 rounded-sm px-2 transition-colors hover:bg-muted/70 focus-within:bg-muted/70",
    embedded
      ? "[@container(max-width:360px)]:h-auto [@container(max-width:360px)]:min-h-12 [@container(max-width:360px)]:flex-col [@container(max-width:360px)]:items-stretch [@container(max-width:360px)]:gap-0.5 [@container(max-width:360px)]:py-1"
      : undefined
  )
  // `a[data-case-field-link]` covers the URL field, whose value renders as a
  // real anchor so cmd-click opens a new tab. Plain anchors are deliberately
  // excluded: clicking blank row space must never navigate away.
  const panelFieldRowInteractiveSelector =
    "input:not([type='hidden']):not([disabled]), textarea:not([disabled]), [role='combobox']:not([aria-disabled='true']), button:not([disabled]), a[data-case-field-link]"
  const panelFieldRowTargetSelector =
    "button:not([disabled]), input:not([type='hidden']):not([disabled]), textarea:not([disabled]), [role='combobox']:not([aria-disabled='true']), a[href]"
  const handlePanelFieldRowClick = (
    event: React.MouseEvent<HTMLDivElement>
  ) => {
    const target = event.target as HTMLElement | null
    if (target?.closest(panelFieldRowTargetSelector)) {
      return
    }

    const controlContainer = event.currentTarget.querySelector<HTMLElement>(
      ".tc-case-panel-row-control"
    )
    const control = controlContainer?.querySelector<HTMLElement>(
      panelFieldRowInteractiveSelector
    )
    if (!control) return

    if (
      control instanceof HTMLInputElement ||
      control instanceof HTMLTextAreaElement
    ) {
      control.focus()
      return
    }

    control.click()
    control.focus()
  }

  const panelSelectTriggerClassName = cn(
    "h-7 w-full min-w-0 max-w-full justify-end border-none px-2 text-right text-sm hover:bg-transparent focus:ring-0 focus-visible:ring-0 focus-visible:ring-offset-0 data-[state=open]:border-none data-[state=open]:ring-0 [&>span]:min-w-0 [&>span]:w-full",
    embedded
      ? "[@container(max-width:360px)]:justify-start [@container(max-width:360px)]:px-0 [@container(max-width:360px)]:text-left"
      : undefined
  )
  const panelControlClassName = cn(
    "tc-case-panel-row-control ml-auto min-w-0 max-w-full flex-1",
    embedded
      ? "[@container(max-width:360px)]:ml-0 [@container(max-width:360px)]:w-full [@container(max-width:360px)]:flex-none"
      : undefined
  )
  const panelLabelClassName = cn(
    "min-w-0 truncate text-sm text-muted-foreground",
    embedded && "[@container(max-width:360px)]:w-full"
  )
  const caseDetailsContent = (
    <>
      <SidebarGroup>
        <SidebarGroupLabel className={CASE_PANEL_GROUP_LABEL_CLASS}>
          Properties
        </SidebarGroupLabel>
        <SidebarGroupContent className="px-2">
          {/* gap-1, not gap-2: grouping is read from the ratio of
              between-section to within-section whitespace. A 4px row gap
              against the ~32px section gap gives ~8:1, so the sections
              separate clearly without spending more vertical space. Keep in
              sync with `case-panel-fields-group.tsx`. */}
          <div className="flex flex-col gap-1">
            <div
              className={panelFieldRowClassName}
              onClick={handlePanelFieldRowClick}
            >
              <span className={panelLabelClassName}>Status</span>
              <div className={panelControlClassName}>
                <StatusSelect
                  status={caseData.status}
                  onValueChange={handleStatusChange}
                  showLabel={false}
                  triggerClassName={panelSelectTriggerClassName}
                  valueClassName="text-sm"
                />
              </div>
            </div>
            <div
              className={panelFieldRowClassName}
              onClick={handlePanelFieldRowClick}
            >
              <span className={panelLabelClassName}>Priority</span>
              <div className={panelControlClassName}>
                <PrioritySelect
                  priority={caseData.priority || "unknown"}
                  onValueChange={handlePriorityChange}
                  showLabel={false}
                  triggerClassName={panelSelectTriggerClassName}
                  valueClassName="text-sm"
                />
              </div>
            </div>
            <div
              className={panelFieldRowClassName}
              onClick={handlePanelFieldRowClick}
            >
              <span className={panelLabelClassName}>Severity</span>
              <div className={panelControlClassName}>
                <SeveritySelect
                  severity={caseData.severity || "unknown"}
                  onValueChange={handleSeverityChange}
                  showLabel={false}
                  triggerClassName={panelSelectTriggerClassName}
                  valueClassName="text-sm"
                />
              </div>
            </div>
            <div
              className={panelFieldRowClassName}
              onClick={handlePanelFieldRowClick}
            >
              <span className={panelLabelClassName}>Assignee</span>
              <div className={panelControlClassName}>
                <AssigneeSelect
                  assignee={caseData.assignee}
                  workspaceMembers={members ?? []}
                  onValueChange={handleAssigneeChange}
                  showLabel={false}
                  triggerClassName={panelSelectTriggerClassName}
                  valueClassName="text-sm"
                />
              </div>
            </div>
            {caseAddonsEnabled &&
              dropdownDefinitions?.map((def: CaseDropdownDefinitionRead) => {
                const currentValue = caseData.dropdown_values?.find(
                  (dv) => dv.definition_id === def.id
                )
                return (
                  <div
                    key={def.id}
                    className={panelFieldRowClassName}
                    onClick={handlePanelFieldRowClick}
                  >
                    <span className={panelLabelClassName} title={def.name}>
                      {def.name}
                    </span>
                    <div className={panelControlClassName}>
                      <CaseDropdownSelect
                        definition={def}
                        currentValue={currentValue}
                        onValueChange={(optionId) =>
                          setDropdownValue.mutate({
                            caseId: caseData.id,
                            definitionId: def.id,
                            optionId,
                          })
                        }
                        showLabel={false}
                        triggerClassName={panelSelectTriggerClassName}
                        valueClassName="text-sm"
                      />
                    </div>
                  </div>
                )
              })}
          </div>
        </SidebarGroupContent>
      </SidebarGroup>
      <CasePanelFieldsGroup
        customFields={customFields}
        visibleCustomFields={visibleCustomFields}
        showAll={showAllCustomFields}
        onToggleShowAll={() => setShowAllCustomFields((prev) => !prev)}
        updateCase={updateCase}
        rowClassName={panelFieldRowClassName}
        labelClassName={panelLabelClassName}
        controlClassName={panelControlClassName}
        inputClassName={cn(
          "w-full min-w-0 max-w-full border-none text-sm hover:bg-transparent focus:ring-0 focus-visible:ring-0 focus-visible:ring-offset-0",
          embedded &&
            "[@container(max-width:360px)]:px-0 [@container(max-width:360px)]:text-left"
        )}
        onRowClick={handlePanelFieldRowClick}
      />
    </>
  )

  return (
    <>
      <CaseWorkflowTrigger caseData={caseData} />
      {/* The case sits on the plain page background, the same surface the nav
          rail and every other route paint. The boxes inside it — tasks,
          comments, duration pills — are the tinted ones, so contrast comes
          from the content rather than from a wash under the whole panel. */}
      <div
        ref={setRootNode}
        className={cn(
          "tc-case-panel flex h-full w-full min-w-0 flex-col",
          embedded && "@container"
        )}
      >
        {/* The switcher band: a plain flex row above the scroll area. Nothing
            scrolls under it, so it needs no sticky positioning, z-index,
            bleed margins, or surface color. In route mode its height is
            intrinsic — pt-6 (24px) above a 32px row of tabs, so 56px — and
            that pt-6 is the only knob for the gap between the header border
            and the tabs; the 56px total feeds the 92px alignment derivation
            on the docked rail below. Both modes now put the tabs inside the
            body's centered column (`mx-auto max-w-4xl` at the same px), so the
            first tab's hit box starts on the case title's left edge at every
            panel width — including wide panels, where the column's centering
            slack `(panelWidth − 896) / 2` used to open the biggest gap. The
            row takes no negative inset: the active tab's tint is the widest
            thing in the band, and letting it bleed past the title reads as a
            misaligned column even when the glyph inside it does not. The
            tradeoff
            is vertical: the tabs no longer stack under the `SidebarTrigger` in
            `nav/controls-header.tsx`, which stays at the 12px chrome inset. */}
        <div
          className={cn(
            "flex shrink-0 items-center",
            embedded ? "h-10" : "pt-6"
          )}
        >
          {embedded ? (
            <div className="min-w-0 flex-1">
              <div className="mx-auto w-full min-w-0 max-w-4xl px-4 [@container(max-width:280px)]:px-3 [@container(max-width:360px)]:px-3.5">
                {/* No sidebar toggle to align to here, so the embedded band
                    keeps the body column's geometry and the first tab's 24px
                    hit box starts on the same edge as the case title below. */}
                <CasePanelSwitcher
                  activePanel={activePanel}
                  onPanelChange={handlePanelChange}
                  idPrefix={panelIdPrefix}
                  hiddenPanelKeys={hiddenPanelKeys}
                  pendingPanelKeys={pendingPanelKeys}
                  taskProgress={taskProgress}
                  tasks={caseTasks}
                  compact
                />
              </div>
            </div>
          ) : (
            <div className="min-w-0 flex-1">
              <div className="mx-auto w-full min-w-0 max-w-4xl px-4 lg:px-6">
                {/* No negative inset: a non-compact tab is min-w-8 around a
                    size-4 icon, so its hit box starts on the title's text edge
                    and the glyph sits 8px inside it. */}
                <CasePanelSwitcher
                  activePanel={activePanel}
                  onPanelChange={handlePanelChange}
                  idPrefix={panelIdPrefix}
                  hiddenPanelKeys={hiddenPanelKeys}
                  pendingPanelKeys={pendingPanelKeys}
                  taskProgress={taskProgress}
                  tasks={caseTasks}
                />
              </div>
            </div>
          )}
          {/* The band spans the whole panel, but the body column below it is
              narrowed by the docked rail. Without this spacer the band's
              `mx-auto max-w-4xl` column centers over the wider box and the
              tabs drift right of the title by half the rail. */}
          {showDockedDetails && (
            <div
              aria-hidden="true"
              className={cn("shrink-0", CASE_DETAILS_RAIL_WIDTH_CLASS)}
            />
          )}
        </div>
        <div className="flex min-h-0 flex-1">
          <div className="min-w-0 flex-1">
            {/* The viewport override neutralizes Radix's `display: table`
                sizing div, whose intrinsic sizing lets any nowrap text (a
                truncating task title, say) inflate the whole body column past
                the viewport instead of truncating. Applied in both modes: the
                body is designed to wrap or truncate, never to scroll
                horizontally. */}
            <ScrollArea
              hideScrollbar
              className="h-full min-w-0 [&_[data-radix-scroll-area-viewport]>div]:!block [&_[data-radix-scroll-area-viewport]>div]:!w-full [&_[data-radix-scroll-area-viewport]>div]:!min-w-0 [&_[data-radix-scroll-area-viewport]>div]:!max-w-full"
            >
              {/* pt-8 holds the title text at 92px from the panel top, given
                  the 56px band outside the scroll area:
                  56 + 32 + (title Input h-9 36px − text-xl 28px line) / 2 =
                  92. The band's own top padding is what sets the gap above the
                  tabs; this one sets the 36px gap between the tabs and the
                  title, so the two are tuned independently. Embedded keeps
                  pt-0 — its 40px compact band already supplies the full
                  offset. */}
              <div
                className={cn(
                  "mx-auto w-full min-w-0 max-w-4xl",
                  embedded
                    ? "px-4 pb-12 [@container(max-width:280px)]:px-3 [@container(max-width:360px)]:px-3.5"
                    : "px-4 pb-16 pt-8 lg:px-6"
                )}
              >
                {/* When the details rail is docked, the gap below the tags comes
                  from the description editor's sticky toolbar padding — see
                  `cases/editor.css`. */}
                <div className="flex flex-col">
                  <div className="py-1.5 first:pt-0 last:pb-0">
                    <CasePanelSummary
                      caseData={caseData}
                      updateCase={updateCase}
                      compact={embedded}
                    />
                  </div>
                  <div className="flex items-start justify-between gap-3 py-1.5 first:pt-0 last:pb-0">
                    <div className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-2.5">
                      {caseData.tags?.length ? (
                        caseData.tags.map((tag) => (
                          <TagBadge key={tag.id} tag={tag} />
                        ))
                      ) : (
                        <span className="text-xs text-muted-foreground">
                          No tags
                        </span>
                      )}
                    </div>
                    <CaseTagPicker
                      caseId={caseId}
                      workspaceId={workspaceId}
                      appliedTags={caseData.tags}
                    />
                  </div>
                </div>

                {/* When the row is too narrow to dock the rail — or the view is
                  embedded in the chat artifact panel — stack the details
                  between the tags and the description so the primary metadata
                  stays reachable rather than sitting below the editor.
                  The pt-12 balances the whitespace above "Properties" against
                  the perceived gap below the bottom border, which includes the
                  description editor's visually empty sticky-toolbar band.
                  The px-0 overrides strip the sidebar primitives' horizontal
                  insets (SidebarGroup p-2, label/content px-2) so the
                  "Properties" label and row text share the title/description
                  left edge; row hover backgrounds still bleed past it via
                  their -mx-2. The docked rail keeps the paddings. */}
                {(showInlineDetails || embedded) && (
                  <div className="mb-4 border-b pt-12 pb-2 [&_[data-sidebar=group-content]]:px-0 [&_[data-sidebar=group-label]]:px-0 [&_[data-sidebar=group]]:px-0">
                    {caseDetailsContent}
                  </div>
                )}

                {/* Exactly one panel at a time; inactive panels unmount. The
                  description supplies its own top whitespace: a 60px sticky
                  toolbar band that is `visibility: hidden` at rest (see
                  cases/editor.css), which lands its first line of text ~68px
                  below the tags. `mt-16` starts every other panel on that same
                  y, so switching tabs does not walk the content up and down
                  the column. */}
                <div
                  role="tabpanel"
                  id={casePanelPanelId(panelIdPrefix, activePanel)}
                  aria-labelledby={casePanelTabId(panelIdPrefix, activePanel)}
                  className={cn(activePanel !== "description" && "mt-16")}
                >
                  <CasePanelContent
                    panel={activePanel}
                    caseId={caseId}
                    workspaceId={workspaceId}
                    caseData={caseData}
                    updateCase={updateCase}
                    embedded={embedded}
                  />
                </div>

                {/* Comments are not a panel: they always render below whichever
                  panel is active, separated by whitespace only — no rule, no
                  heading. The section landmark hands AT the boundary that the
                  missing heading would have marked. Nothing pins over the top
                  of the scroll viewport anymore, so anchored scrolls need no
                  offset. */}
                <section
                  aria-label="Comments"
                  className={embedded ? "mt-10" : "mt-16"}
                >
                  <CommentSection caseId={caseId} workspaceId={workspaceId} />
                </section>
              </div>
            </ScrollArea>
          </div>
          {showDockedDetails && (
            <Sidebar
              side="right"
              collapsible="none"
              className={cn(
                "shrink-0 bg-transparent text-foreground",
                CASE_DETAILS_RAIL_WIDTH_CLASS
              )}
            >
              <SidebarContent className="h-full">
                {/* pt-5 (20px) lands the "Properties" label text flush with
                    the case title text, both 92px from the panel top. Both
                    columns sit under the shared 56px switcher band:
                    - main column: body wrapper pt-8 (32px) + title Input h-9
                      (36px) with a text-xl 28px line → text top =
                      56 + 32 + (36 − 28) / 2 = 92px
                    - rail: pt-5 (20px) + SidebarGroup p-2 (8px) +
                      SidebarGroupLabel h-8 (32px) with a text-xs 16px line →
                      text top = 56 + 20 + 8 + (32 − 16) / 2 = 92px */}
                <div className="px-2 pt-5">{caseDetailsContent}</div>
              </SidebarContent>
            </Sidebar>
          )}
        </div>
      </div>
      {closureDialog && (
        <CaseClosureDialog
          open={closureDialog.open}
          onOpenChange={(open) => {
            if (!open) setClosureDialog(null)
          }}
          targetStatus={closureDialog.targetStatus as "closed" | "resolved"}
          requiredFields={
            caseFieldDefinitions?.filter(
              (f) => !f.reserved && f.required_on_closure
            ) ?? []
          }
          requiredDropdowns={
            dropdownDefinitions?.filter((d) => d.required_on_closure) ?? []
          }
          currentFieldValues={Object.fromEntries(
            caseData.fields
              .filter((f) => !f.reserved)
              .map((f) => [f.id, f.value])
          )}
          currentDropdownValues={caseData.dropdown_values}
          onSubmit={async (data) => {
            await updateCase({
              status: closureDialog.targetStatus,
              fields: data.fields,
              dropdown_values: data.dropdown_values.map((dv) => ({
                definition_id: dv.definition_id,
                option_id: dv.option_id,
              })),
            })
          }}
        />
      )}
    </>
  )
}
