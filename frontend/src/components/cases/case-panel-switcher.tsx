"use client"

import {
  type KeyboardEvent as ReactKeyboardEvent,
  type ReactNode,
  useRef,
} from "react"
import type { CaseTaskRead } from "@/client"
import {
  CASE_PANELS,
  type CasePanelDefinition,
  type CasePanelKey,
  casePanelPanelId,
  casePanelTabId,
} from "@/components/cases/case-panels"
import { CaseTaskProgressRing } from "@/components/cases/case-task-progress-ring"
import {
  CaseTaskStatusIcon,
  sortCaseTasksByUrgency,
} from "@/components/cases/case-task-status"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Kbd } from "@/components/ui/kbd"
import { ScrollArea } from "@/components/ui/scroll-area"
import { parseShortcutKeys } from "@/lib/tiptap-utils"
import { cn } from "@/lib/utils"

/** Completed/total task counts shown on the Tasks button. */
export interface CaseTaskProgress {
  done: number
  total: number
}

/** Props for {@link CasePanelSwitcher}. */
export interface CasePanelSwitcherProps {
  /** Panel whose tab renders selected. */
  activePanel: CasePanelKey
  /** Called with the clicked panel's key. */
  onPanelChange: (panel: CasePanelKey) => void
  /**
   * Unique prefix (from `useId()`) for tab/tabpanel DOM ids, so the case
   * route and an embedded chat artifact instance never collide.
   */
  idPrefix: string
  /** Task counts for the Tasks button ring. Zero total renders the plain icon. */
  taskProgress?: CaseTaskProgress
  /**
   * The case's tasks, previewed in the Tasks button's hover card. Undefined or
   * empty falls back to the plain label card, which also covers the loading
   * and entitlement-denied cases.
   */
  tasks?: CaseTaskRead[]
  /**
   * Embedded (chat artifact) density: `size-6` buttons, ring only with no
   * count text, and no shortcut hints — digit shortcuts bind route-side only.
   */
  compact?: boolean
  /** Extra classes for the tablist row (optical alignment at the call site). */
  className?: string
}

/**
 * The case panel switcher: a right-alignable row of icon tabs, one per panel.
 *
 * Hand-rolled tablist rather than Radix `Tabs`: the tabpanel lives ~200 lines
 * away in `case-panel-view.tsx`, and Radix would drag the title, tags, and
 * comments into one `<Tabs>` root while activating on every arrow press.
 * Roving tabindex with manual activation — arrows move focus without
 * selecting, Enter/Space activate. Icon-only at every width by design, and no
 * overflow scroll: compact's six buttons total 154px (6×24 + 5×2), which fits
 * even the embedded panel's 280px floor, while the route band's 232px (6×32 +
 * 5×8) sits in a row that spans the full panel width. Knows nothing of
 * routing, queries, or entitlements — the view resolves those and passes
 * results in; every panel always gets a button, and an entitlement-locked
 * press is the view's `onPanelChange` to intercept.
 */
export function CasePanelSwitcher({
  activePanel,
  onPanelChange,
  idPrefix,
  taskProgress,
  tasks,
  compact = false,
  className,
}: CasePanelSwitcherProps) {
  const tablistRef = useRef<HTMLDivElement>(null)

  function handleTablistKeyDown(event: ReactKeyboardEvent<HTMLDivElement>) {
    const { key } = event
    if (
      key !== "ArrowLeft" &&
      key !== "ArrowRight" &&
      key !== "Home" &&
      key !== "End"
    ) {
      return
    }
    const tabs = Array.from(
      tablistRef.current?.querySelectorAll<HTMLButtonElement>("[role='tab']") ??
        []
    )
    if (tabs.length === 0) {
      return
    }
    const currentIndex = tabs.findIndex((tab) => tab === document.activeElement)
    let nextIndex: number
    if (key === "Home") {
      nextIndex = 0
    } else if (key === "End") {
      nextIndex = tabs.length - 1
    } else if (key === "ArrowLeft") {
      nextIndex = (currentIndex - 1 + tabs.length) % tabs.length
    } else {
      nextIndex = (currentIndex + 1) % tabs.length
    }
    event.preventDefault()
    // Move focus only — manual activation. Automatic activation would fire a
    // `router.replace` per arrow press and remount the active panel.
    tabs[nextIndex]?.focus()
  }

  return (
    <div
      ref={tablistRef}
      role="tablist"
      aria-label="Case sections"
      aria-orientation="horizontal"
      className={cn(
        "flex items-center",
        compact ? "gap-0.5" : "gap-2",
        className
      )}
      onKeyDown={handleTablistKeyDown}
    >
      {CASE_PANELS.map((definition) => (
        <CasePanelTabButton
          key={definition.key}
          definition={definition}
          selected={definition.key === activePanel}
          onSelect={onPanelChange}
          idPrefix={idPrefix}
          taskProgress={definition.key === "tasks" ? taskProgress : undefined}
          tasks={definition.key === "tasks" ? tasks : undefined}
          compact={compact}
        />
      ))}
    </div>
  )
}

interface CasePanelTabButtonProps {
  definition: CasePanelDefinition
  selected: boolean
  onSelect: (panel: CasePanelKey) => void
  idPrefix: string
  taskProgress?: CaseTaskProgress
  tasks?: CaseTaskRead[]
  compact: boolean
}

function CasePanelTabButton({
  definition,
  selected,
  onSelect,
  idPrefix,
  taskProgress,
  tasks,
  compact,
}: CasePanelTabButtonProps) {
  const Icon = definition.icon
  // Zero tasks and still-loading both render the plain icon: an empty ring
  // plus `0/0` is noise and would flash on every case open.
  const showRing = (taskProgress?.total ?? 0) > 0
  const showCount = showRing && !compact
  // The hover card is mouse-only and never announced, so it cannot be the
  // accessible name; the count also folds in here for AT.
  const ariaLabel =
    showRing && taskProgress
      ? `${definition.label}, ${taskProgress.done} of ${taskProgress.total} done`
      : definition.label

  // Two cards behind one trigger. The Tasks tab previews its list when it has
  // one; every other tab — and Tasks itself while the query is empty, loading,
  // or entitlement-denied — keeps the label + shortcut card. Resolved in an
  // if/else rather than inline, so the JSX below stays a single expression.
  let hoverCardContent: ReactNode
  if (tasks?.length) {
    hoverCardContent = (
      // p-1 overrides the p-4 default: the rows carry their own px-2 py-1, so
      // the card's padding is just the 4px gutter around the list. Width stays
      // the default w-64 — fixed, so a long title truncates instead of
      // stretching the card to the panel's width.
      <HoverCardContent side="bottom" align="start" className="w-64 p-1">
        {/* Two caps, one height: max-h-56 on the root clips the card, and the
            same cap on Radix's viewport is what actually makes it scroll — a
            percentage height (`size-full`) against the root's auto height
            resolves to auto, so without it the overflow is simply cut off.
            The inner override neutralizes Radix's `display: table` sizing div,
            whose intrinsic sizing would let a nowrap title inflate the card
            past w-64 instead of truncating. */}
        <ScrollArea
          hideScrollbar
          className="max-h-56 [&_[data-radix-scroll-area-viewport]]:max-h-56 [&_[data-radix-scroll-area-viewport]>div]:!block [&_[data-radix-scroll-area-viewport]>div]:!w-full [&_[data-radix-scroll-area-viewport]>div]:!min-w-0 [&_[data-radix-scroll-area-viewport]>div]:!max-w-full"
        >
          {sortCaseTasksByUrgency(tasks).map((task) => (
            <div
              key={task.id}
              className="flex min-w-0 items-center gap-2 rounded-sm px-2 py-1"
            >
              <CaseTaskStatusIcon
                status={task.status}
                className="size-3.5 shrink-0"
              />
              <span className="truncate text-xs">{task.title}</span>
            </div>
          ))}
        </ScrollArea>
      </HoverCardContent>
    )
  } else {
    hoverCardContent = (
      <HoverCardContent
        side="bottom"
        className="flex w-auto items-center gap-1.5 px-2 py-1"
      >
        <span className="text-xs">{definition.label}</span>
        {compact && showRing && taskProgress && (
          <span className="text-xs tabular-nums text-muted-foreground">
            {taskProgress.done}/{taskProgress.total}
          </span>
        )}
        {!compact &&
          parseShortcutKeys({
            shortcutKeys: String(definition.shortcut),
          }).map((shortcutKey) => <Kbd key={shortcutKey}>{shortcutKey}</Kbd>)}
      </HoverCardContent>
    )
  }

  return (
    <HoverCard openDelay={350} closeDelay={100}>
      <HoverCardTrigger asChild>
        <button
          type="button"
          role="tab"
          id={casePanelTabId(idPrefix, definition.key)}
          aria-controls={casePanelPanelId(idPrefix, definition.key)}
          aria-selected={selected}
          aria-label={ariaLabel}
          tabIndex={selected ? 0 : -1}
          onClick={() => onSelect(definition.key)}
          className={cn(
            "flex shrink-0 items-center justify-center rounded-md transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring",
            compact ? "h-6 min-w-6" : "h-8 min-w-8",
            showCount && "gap-1 px-1.5",
            selected
              ? "bg-accent text-foreground"
              : "text-muted-foreground hover:bg-accent/60 hover:text-foreground"
          )}
        >
          {showRing && taskProgress ? (
            <CaseTaskProgressRing
              done={taskProgress.done}
              total={taskProgress.total}
              className={compact ? "size-3" : undefined}
            />
          ) : (
            <Icon className={compact ? "size-3.5" : "size-4"} />
          )}
          {showCount && taskProgress && (
            <span className="text-xs tabular-nums leading-none">
              {taskProgress.done}/{taskProgress.total}
            </span>
          )}
        </button>
      </HoverCardTrigger>
      {hoverCardContent}
    </HoverCard>
  )
}
