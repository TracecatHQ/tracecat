"use client"

import { useVirtualizer } from "@tanstack/react-virtual"
import {
  AlertTriangleIcon,
  LoaderIcon,
  PinIcon,
  RotateCcwIcon,
  Search,
  XIcon,
} from "lucide-react"
import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react"
import type { WorkflowRerunActionMatch, WorkflowRerunPlan } from "@/client"
import { getIcon } from "@/components/icons"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog"
import { Kbd } from "@/components/ui/kbd"
import { useWorkflowDefinitions } from "@/hooks/use-workflow-definitions"
import {
  useRerunWorkflowExecution,
  useWorkflowRerunPreview,
} from "@/hooks/use-workflow-rerun"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

const DRAFT_TARGET = "draft"
const ROW_HEIGHT = 44
const REVEAL =
  "opacity-0 group-hover:opacity-100 group-has-[:focus-visible]:opacity-100 focus-visible:opacity-100"

type RerunFilter = "all" | "fixed" | "runs" | "unmatched"

const FILTERS: { id: RerunFilter; title: string }[] = [
  { id: "all", title: "All actions" },
  { id: "fixed", title: "Fixed" },
  { id: "runs", title: "Runs again" },
  { id: "unmatched", title: "Can't reuse" },
]

/** Rerun a past execution against the draft or a published version. */
export function WorkflowRerunDialog({
  executionId,
  workflowId,
  open,
  onOpenChange,
  onStarted,
  rerunFromRef,
}: {
  executionId: string
  workflowId: string
  open: boolean
  onOpenChange: (open: boolean) => void
  onStarted: (wfExecId: string) => void
  /** Open with this action and everything downstream of it running again. */
  rerunFromRef?: string | null
}) {
  const searchRef = useRef<HTMLInputElement>(null)
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {open && (
        <DialogContent
          aria-describedby={undefined}
          onOpenAutoFocus={(event) => {
            event.preventDefault()
            searchRef.current?.focus()
          }}
          className="flex h-[min(88dvh,800px)] w-[min(96vw,1120px)] max-w-none flex-col gap-0 overflow-hidden p-0 shadow-none [&>button]:hidden"
        >
          <DialogTitle className="sr-only">
            Rerun with fixed results
          </DialogTitle>
          <RerunBody
            executionId={executionId}
            workflowId={workflowId}
            initialRerunFromRef={rerunFromRef ?? null}
            searchRef={searchRef}
            onCancel={() => onOpenChange(false)}
            onStarted={(wfExecId) => {
              onStarted(wfExecId)
              onOpenChange(false)
            }}
          />
        </DialogContent>
      )}
    </Dialog>
  )
}

function rowGroup(
  action: WorkflowRerunActionMatch,
  selected: Set<string>
): Exclude<RerunFilter, "all"> {
  if (action.status === "not_in_target" || action.status === "action_changed") {
    return "unmatched"
  }
  return selected.has(action.ref) ? "fixed" : "runs"
}

const GROUP_ORDER = { unmatched: 0, runs: 1, fixed: 2 } as const

function RerunBody({
  executionId,
  workflowId,
  initialRerunFromRef,
  searchRef,
  onCancel,
  onStarted,
}: {
  executionId: string
  workflowId: string
  initialRerunFromRef: string | null
  searchRef: React.RefObject<HTMLInputElement>
  onCancel: () => void
  onStarted: (wfExecId: string) => void
}) {
  const workspaceId = useWorkspaceId()
  const [target, setTarget] = useState<string>(DRAFT_TARGET)
  const [rerunFromRef, setRerunFromRef] = useState(initialRerunFromRef)
  const [filter, setFilter] = useState<RerunFilter>("all")
  const [query, setQuery] = useState("")
  const deferredQuery = useDeferredValue(query.trim().toLowerCase())
  const useDraft = target === DRAFT_TARGET
  const version = useDraft ? null : Number(target)

  const { definitions } = useWorkflowDefinitions(workspaceId, workflowId)
  const { rerunPlan, rerunPlanIsLoading, rerunPlanError } =
    useWorkflowRerunPreview(workspaceId, executionId, {
      use_draft: useDraft,
      version,
      rerun_from_ref: rerunFromRef,
    })
  const { rerunExecution, rerunExecutionIsPending } = useRerunWorkflowExecution(
    workspaceId,
    executionId
  )

  const [selected, setSelected] = useState<Set<string>>(new Set())
  useEffect(() => {
    setSelected(new Set(rerunPlan?.fixed_action_refs ?? []))
  }, [rerunPlan])
  const fixable = useMemo(
    () => new Set(rerunPlan?.fixed_action_refs ?? []),
    [rerunPlan]
  )

  const counts = useMemo(() => {
    const result = { all: 0, fixed: 0, runs: 0, unmatched: 0 }
    for (const action of rerunPlan?.actions ?? []) {
      result.all += 1
      result[rowGroup(action, selected)] += 1
    }
    return result
  }, [rerunPlan, selected])

  // Grouping uses the plan's own fixed set, so toggling a row doesn't move it.
  const rows = useMemo(() => {
    const actions = (rerunPlan?.actions ?? []).filter(
      (action) =>
        (filter === "all" || rowGroup(action, selected) === filter) &&
        (!deferredQuery ||
          action.ref.toLowerCase().includes(deferredQuery) ||
          (action.target_action ?? action.source_action ?? "")
            .toLowerCase()
            .includes(deferredQuery))
    )
    return actions.sort(
      (a, b) =>
        GROUP_ORDER[rowGroup(a, fixable)] - GROUP_ORDER[rowGroup(b, fixable)]
    )
  }, [rerunPlan, filter, selected, fixable, deferredQuery])

  const listRef = useRef<HTMLDivElement>(null)
  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => listRef.current,
    estimateSize: () => ROW_HEIGHT,
    getItemKey: (i) => rows[i].ref,
    overscan: 8,
  })

  function toggle(ref: string) {
    setSelected((prev) => {
      const next = new Set(prev)
      if (next.has(ref)) next.delete(ref)
      else next.add(ref)
      return next
    })
  }

  async function handleRun() {
    const result = await rerunExecution({
      use_draft: useDraft,
      version,
      fixed_action_refs: [...selected].sort(),
      rerun_from_ref: rerunFromRef,
    })
    onStarted(result.wf_exec_id)
  }

  const runDisabled =
    !rerunPlan ||
    rerunPlanIsLoading ||
    Boolean(rerunPlan.inputs_error) ||
    rerunExecutionIsPending

  return (
    <>
      <div className="flex h-12 shrink-0 items-center gap-3 border-b px-4">
        <Search className="size-4 text-muted-foreground" />
        <input
          ref={searchRef}
          aria-label="Search actions"
          value={query}
          onChange={(event) => {
            setQuery(event.target.value)
            listRef.current?.scrollTo({ top: 0 })
          }}
          placeholder={`Search ${counts.all.toLocaleString()} actions`}
          className="h-9 min-w-0 flex-1 border-0 bg-transparent text-sm outline-none focus-visible:ring-0"
        />
        {(rerunPlanIsLoading || rerunExecutionIsPending) && (
          <LoaderIcon className="size-4 animate-spin text-muted-foreground" />
        )}
        <Kbd>esc</Kbd>
      </div>
      <div className="flex min-h-0 flex-1">
        <nav
          aria-label="Rerun options"
          className="w-48 shrink-0 overflow-y-auto border-r py-2 md:w-60"
        >
          <RailLabel>Show</RailLabel>
          {FILTERS.map((item) => (
            <RailButton
              key={item.id}
              active={filter === item.id}
              onClick={() => {
                setFilter(item.id)
                listRef.current?.scrollTo({ top: 0 })
              }}
              count={counts[item.id]}
              warn={item.id === "unmatched" && counts.unmatched > 0}
            >
              {item.title}
            </RailButton>
          ))}
          <RailLabel>Run against</RailLabel>
          <RailButton active={useDraft} onClick={() => setTarget(DRAFT_TARGET)}>
            Draft
          </RailButton>
          {definitions?.map((defn) => (
            <RailButton
              key={defn.version}
              active={target === String(defn.version)}
              onClick={() => setTarget(String(defn.version))}
            >
              Version {defn.version}
            </RailButton>
          ))}
        </nav>
        <div className="flex min-w-0 flex-1 flex-col">
          <PlanNotices
            plan={rerunPlan}
            error={rerunPlanError?.body}
            rerunFromRef={rerunFromRef}
            onClearRerunFrom={() => setRerunFromRef(null)}
          />
          <div
            ref={listRef}
            role="list"
            aria-label="Actions"
            className="min-h-0 flex-1 overflow-auto"
          >
            <div
              style={{
                height: virtualizer.getTotalSize(),
                position: "relative",
                width: "100%",
              }}
            >
              {virtualizer.getVirtualItems().map((virtualRow) => {
                const action = rows[virtualRow.index]
                return (
                  <div
                    key={virtualRow.key}
                    role="listitem"
                    style={{
                      position: "absolute",
                      top: 0,
                      left: 0,
                      width: "100%",
                      height: virtualRow.size,
                      transform: `translateY(${virtualRow.start}px)`,
                    }}
                  >
                    <ActionRow
                      action={action}
                      fixable={fixable.has(action.ref)}
                      fixed={selected.has(action.ref)}
                      isRerunFrom={rerunFromRef === action.ref}
                      onToggle={toggle}
                      onRerunFrom={setRerunFromRef}
                    />
                  </div>
                )
              })}
            </div>
            {rerunPlan && rows.length === 0 && (
              <p className="p-4 text-sm text-muted-foreground">
                No actions found.
              </p>
            )}
          </div>
        </div>
      </div>
      <div className="flex h-14 shrink-0 items-center gap-2 border-t px-4 text-xs">
        <span>
          {selected.size} of {fixable.size} reusable{" "}
          {fixable.size === 1 ? "result" : "results"} fixed
        </span>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 px-2 text-xs"
          disabled={fixable.size === 0 || selected.size === fixable.size}
          onClick={() => setSelected(new Set(fixable))}
        >
          Fix all
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-7 px-2 text-xs"
          disabled={selected.size === 0}
          onClick={() => setSelected(new Set())}
        >
          Fix none
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="ml-auto"
          onClick={onCancel}
        >
          Cancel
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={handleRun}
          disabled={runDisabled}
        >
          {selected.size > 0 ? `Rerun with ${selected.size} fixed` : "Rerun"}
        </Button>
      </div>
    </>
  )
}

function RailLabel({ children }: { children: React.ReactNode }) {
  return (
    <p className="flex h-9 items-end px-4 pb-1 text-xs text-muted-foreground">
      {children}
    </p>
  )
}

function RailButton({
  active,
  onClick,
  count,
  warn,
  children,
}: {
  active: boolean
  onClick: () => void
  count?: number
  warn?: boolean
  children: React.ReactNode
}) {
  return (
    <button
      type="button"
      aria-current={active ? "true" : undefined}
      onClick={onClick}
      className={cn(
        "flex h-9 w-full items-center gap-2 px-4 text-left text-xs hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring",
        active && "bg-muted"
      )}
    >
      <span className="min-w-0 flex-1 truncate">{children}</span>
      {count !== undefined && (
        <span
          className={cn(
            "shrink-0 font-mono text-[11px] text-muted-foreground",
            warn && "text-amber-600 dark:text-amber-400"
          )}
        >
          {count.toLocaleString()}
        </span>
      )}
    </button>
  )
}

function PlanNotices({
  plan,
  error,
  rerunFromRef,
  onClearRerunFrom,
}: {
  plan?: WorkflowRerunPlan
  error?: unknown
  rerunFromRef: string | null
  onClearRerunFrom: () => void
}) {
  const notices: React.ReactNode[] = []
  if (error) {
    notices.push(
      <Notice key="error" tone="error">
        Could not match this run: {getErrorMessage(error)}
      </Notice>
    )
  }
  if (plan?.inputs_error) {
    notices.push(
      <Notice key="inputs" tone="error">
        <span className="whitespace-pre-wrap">
          Inputs don&apos;t match the target: {plan.inputs_error}
        </span>
      </Notice>
    )
  }
  if (plan && plan.unmatched_refs.length > 0) {
    notices.push(
      <Notice key="unmatched" tone="warn">
        {plan.unmatched_refs.length}{" "}
        {plan.unmatched_refs.length === 1 ? "result" : "results"} can&apos;t be
        reused: the target has no matching action.
      </Notice>
    )
  }
  if (rerunFromRef) {
    notices.push(
      <div
        key="from"
        className="flex items-center gap-2 border-b px-4 py-2 text-xs"
      >
        <RotateCcwIcon className="size-3.5 text-muted-foreground" />
        <span>
          Rerunning from <span className="font-mono">{rerunFromRef}</span> and
          everything after it
        </span>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="ml-auto h-6 gap-1 px-2 text-xs"
          onClick={onClearRerunFrom}
        >
          <XIcon className="size-3" />
          Clear
        </Button>
      </div>
    )
  }
  return <>{notices}</>
}

function Notice({
  tone,
  children,
}: {
  tone: "error" | "warn"
  children: React.ReactNode
}) {
  return (
    <div
      className={cn(
        "flex items-start gap-2 border-b px-4 py-2 text-xs",
        tone === "error"
          ? "bg-rose-500/10 text-rose-700 dark:text-rose-400"
          : "bg-amber-500/10 text-amber-700 dark:text-amber-400"
      )}
    >
      <AlertTriangleIcon className="mt-0.5 size-3.5 shrink-0" />
      <div className="min-w-0">{children}</div>
    </div>
  )
}

function ActionRow({
  action,
  fixable,
  fixed,
  isRerunFrom,
  onToggle,
  onRerunFrom,
}: {
  action: WorkflowRerunActionMatch
  fixable: boolean
  fixed: boolean
  isRerunFrom: boolean
  onToggle: (ref: string) => void
  onRerunFrom: (ref: string) => void
}) {
  const actionType = action.target_action ?? action.source_action ?? ""
  const canRerunFrom =
    action.status !== "not_in_target" && !isRerunFrom && action.target_action
  return (
    <div
      className={cn(
        "group flex h-11 items-center gap-3 border-b border-border/50 px-4 text-xs",
        fixable && "cursor-pointer hover:bg-muted/50"
      )}
      onClick={fixable ? () => onToggle(action.ref) : undefined}
    >
      <span className="flex w-4 shrink-0 items-center justify-center">
        {fixable && (
          <Checkbox
            aria-label={`Fix ${action.ref}`}
            checked={fixed}
            onClick={(event) => event.stopPropagation()}
            onCheckedChange={() => onToggle(action.ref)}
          />
        )}
      </span>
      {getIcon(actionType, { className: "size-6 shrink-0 p-1" })}
      <div className="flex min-w-0 flex-1 items-baseline gap-2">
        <span className="truncate font-mono">{action.ref}</span>
        <span className="truncate text-muted-foreground">{actionType}</span>
      </div>
      {canRerunFrom && (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className={cn("h-6 shrink-0 gap-1 px-2 text-xs", REVEAL)}
          onClick={(event) => {
            event.stopPropagation()
            onRerunFrom(action.ref)
          }}
        >
          <RotateCcwIcon className="size-3" />
          Rerun from here
        </Button>
      )}
      <RerunStatus action={action} fixed={fixed} />
    </div>
  )
}

function RerunStatus({
  action,
  fixed,
}: {
  action: WorkflowRerunActionMatch
  fixed: boolean
}) {
  const badge = "h-5 shrink-0 px-2 text-[10px] font-normal"
  switch (action.status) {
    case "not_in_target":
      return (
        <Badge variant="outline" className={cn(badge, "text-amber-600")}>
          Not in target
        </Badge>
      )
    case "action_changed":
      return (
        <Badge
          variant="outline"
          className={cn(badge, "text-amber-600")}
          title={action.reason ?? undefined}
        >
          Action changed
        </Badge>
      )
    default:
      if (fixed) {
        return (
          <Badge variant="secondary" className={cn(badge, "gap-1")}>
            <PinIcon className="size-2.5" />
            Fixed
          </Badge>
        )
      }
      return (
        <span
          className="max-w-56 shrink-0 truncate text-muted-foreground"
          title={action.reason ?? undefined}
        >
          {action.reason ? `Runs · ${action.reason}` : "Runs"}
        </span>
      )
  }
}

function getErrorMessage(body: unknown): string {
  if (body && typeof body === "object" && "detail" in body) {
    const detail = body.detail
    if (typeof detail === "string") {
      return detail
    }
    if (
      detail &&
      typeof detail === "object" &&
      "message" in detail &&
      typeof detail.message === "string"
    ) {
      return detail.message
    }
  }
  return "Unexpected error"
}
