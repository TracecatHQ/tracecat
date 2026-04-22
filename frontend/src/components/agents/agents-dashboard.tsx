"use client"

import { useQueryClient } from "@tanstack/react-query"
import { format } from "date-fns"
import {
  AlertTriangleIcon,
  ArrowDownIcon,
  ArrowUpIcon,
  BoxIcon,
  Calendar,
  ChevronDownIcon,
  Clock3,
  Copy,
  CopyPlus,
  ExternalLinkIcon,
  FolderIcon,
  FolderKanban,
  Leaf,
  ListIcon,
  MousePointerClickIcon,
  Pencil,
  SearchIcon,
  TagsIcon,
  Trash2,
  Type,
  WorkflowIcon,
} from "lucide-react"
import Link from "next/link"
import { useRouter, useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useState } from "react"
import type {
  AgentFolderDirectoryItem,
  AgentPresetDirectoryItem,
  AgentPresetReadMinimal,
  AgentTagRead,
  ApprovalRead,
} from "@/client"
import {
  agentPresetsAddPresetTag,
  agentPresetsGetAgentPreset,
  agentPresetsListAgentPresets,
  agentPresetsRemovePresetTag,
} from "@/client"
import { AgentApprovalsDialog } from "@/components/agents/agent-approvals-dialog"
import { CollapsibleSection } from "@/components/collapsible-section"
import { CopyButton } from "@/components/copy-button"
import {
  FileTreeCommand,
  getFileTreeItems,
  ROOT_FOLDER_NAME,
} from "@/components/dashboard/file-tree-command"
import { getIcon } from "@/components/icons"
import { JsonViewWithControls } from "@/components/json-viewer"
import { CenteredSpinner, Spinner } from "@/components/loading/spinner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import {
  ContextMenu,
  ContextMenuCheckboxItem,
  ContextMenuContent,
  ContextMenuGroup,
  ContextMenuItem,
  ContextMenuPortal,
  ContextMenuSeparator,
  ContextMenuSub,
  ContextMenuSubContent,
  ContextMenuSubTrigger,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import {
  type AgentDirectoryItem,
  useAgentDirectoryItems,
  useAgentFolders,
  useAgentPresets,
  useAgentTagCatalog,
  useCreateAgentPreset,
  useDeleteAgentPreset,
  useMoveAgentPreset,
} from "@/hooks/use-agent-presets"
import { buildDuplicateAgentPresetPayload } from "@/lib/agent-presets"
import type { AgentSessionWithStatus, AgentStatusTone } from "@/lib/agents"
import {
  compareAgentStatusPriority,
  getAgentStatusMetadata,
} from "@/lib/agents"
import type { TracecatApiError } from "@/lib/errors"
import { executionId as splitExecutionId } from "@/lib/event-history"
import { cn, reconstructActionType, shortTimeAgo } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

const toneIndicatorClasses: Record<AgentStatusTone, string> = {
  danger: "bg-rose-500 shadow-[0_0_0_1.5px_rgba(244,63,94,0.35)]",
  warning: "bg-amber-500 shadow-[0_0_0_1.5px_rgba(245,158,11,0.45)]",
  success: "bg-emerald-500 shadow-[0_0_0_1.5px_rgba(16,185,129,0.35)]",
  info: "bg-sky-500 shadow-[0_0_0_1.5px_rgba(14,165,233,0.3)]",
  neutral: "bg-muted-foreground/50 shadow-[0_0_0_1.5px_rgba(113,113,122,0.25)]",
}

const toneBadgeClasses: Record<AgentStatusTone, string> = {
  danger: "bg-rose-50 text-rose-600 border border-rose-200",
  warning: "bg-amber-50 text-amber-700 border border-amber-200",
  success: "bg-emerald-50 text-emerald-600 border border-emerald-200",
  info: "bg-sky-50 text-sky-600 border border-sky-200",
  neutral: "bg-muted text-muted-foreground border border-border/60",
}

type AgentsBoardProps = {
  sessions?: AgentSessionWithStatus[]
  isLoading: boolean
  error: TracecatApiError | null
  onRetry: () => void
}

export function AgentsBoard({
  sessions,
  isLoading,
  error,
  onRetry,
}: AgentsBoardProps) {
  const [dialogSession, setDialogSession] =
    useState<AgentSessionWithStatus | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)

  const groupedSessions = useMemo(() => {
    if (!sessions || sessions.length === 0) {
      return []
    }
    const groups = new Map<
      AgentSessionWithStatus["derivedStatus"],
      AgentSessionWithStatus[]
    >()
    for (const session of sessions) {
      const bucket = groups.get(session.derivedStatus) ?? []
      bucket.push(session)
      groups.set(session.derivedStatus, bucket)
    }

    return Array.from(groups.entries())
      .map(([status, items]) => ({
        status,
        items: items.sort(
          (a, b) =>
            new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
        ),
      }))
      .sort((a, b) => compareAgentStatusPriority(a.status, b.status))
  }, [sessions])

  if (isLoading) {
    return (
      <div className="flex h-full w-full items-center justify-center py-20">
        <Spinner className="size-8" />
      </div>
    )
  }

  if (error) {
    const detail =
      typeof error.body?.detail === "string" ? error.body.detail : undefined
    const message =
      detail ??
      error.message ??
      "Something went wrong while fetching the latest agent sessions."

    return (
      <div className="flex h-full w-full items-center justify-center py-10">
        <Alert variant="destructive" className="max-w-xl">
          <AlertTriangleIcon className="size-4" />
          <AlertTitle>Unable to load agents</AlertTitle>
          <AlertDescription className="flex flex-col gap-3 text-sm">
            <span>{message}</span>
            <div>
              <Button variant="secondary" size="sm" onClick={onRetry}>
                Try again
              </Button>
            </div>
          </AlertDescription>
        </Alert>
      </div>
    )
  }

  if (groupedSessions.length === 0) {
    return (
      <Empty>
        <EmptyHeader>
          <EmptyMedia variant="icon">
            <Leaf className="size-5 text-muted-foreground/60" />
          </EmptyMedia>
          <EmptyTitle>No agent activity yet.</EmptyTitle>
          <EmptyDescription>
            When agents run, they will appear here grouped by their latest
            status.
          </EmptyDescription>
        </EmptyHeader>
      </Empty>
    )
  }

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-4">
      {groupedSessions.map(({ status, items }, index) => {
        const metadata = getAgentStatusMetadata(
          status as AgentSessionWithStatus["derivedStatus"]
        )
        return (
          <CollapsibleSection
            key={status}
            node={
              <div className="flex items-center gap-3">
                <div className="flex items-center gap-2 text-sm font-medium">
                  <StatusIndicator tone={metadata.tone} />
                  <span>{metadata.label}</span>
                </div>
                <span
                  className={cn(
                    "rounded-full px-2 py-0.5 text-xs",
                    toneBadgeClasses[metadata.tone]
                  )}
                >
                  {items.length}
                </span>
              </div>
            }
            size="lg"
            iconSize="lg"
            showToggleText={false}
            defaultIsOpen={index === 0}
          >
            <div className="space-y-2 pb-1">
              {items.map((session) => (
                <AgentSessionCard
                  key={session.id}
                  session={session}
                  onReviewApprovals={() => {
                    setDialogSession(session)
                    setDialogOpen(true)
                  }}
                />
              ))}
            </div>
          </CollapsibleSection>
        )
      })}
      <AgentApprovalsDialog
        session={dialogSession}
        open={dialogOpen}
        onOpenChange={(open) => {
          setDialogOpen(open)
          if (!open) {
            setDialogSession(null)
          }
        }}
        onSubmitted={onRetry}
      />
    </div>
  )
}

function StatusIndicator({
  tone,
  className,
}: {
  tone: AgentStatusTone
  className?: string
}) {
  return (
    <span
      className={cn(
        "inline-flex size-3 rounded-full",
        toneIndicatorClasses[tone],
        className
      )}
      aria-hidden="true"
    />
  )
}

function isEmptyObjectOrArray(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.length === 0
  }
  if (value && typeof value === "object") {
    return Object.keys(value).length === 0
  }
  return false
}

function normalizePayload(raw: unknown): {
  value: unknown
  hasValue: boolean
} {
  if (raw === null || raw === undefined) {
    return { value: null, hasValue: false }
  }

  if (typeof raw === "string") {
    const trimmed = raw.trim()
    if (!trimmed) {
      return { value: null, hasValue: false }
    }
    try {
      const parsed = JSON.parse(trimmed)
      if (parsed === null) {
        return { value: null, hasValue: false }
      }
      return { value: parsed, hasValue: !isEmptyObjectOrArray(parsed) }
    } catch {
      return { value: trimmed, hasValue: true }
    }
  }

  if (Array.isArray(raw)) {
    return { value: raw, hasValue: raw.length > 0 }
  }

  if (typeof raw === "object") {
    return { value: raw, hasValue: !isEmptyObjectOrArray(raw) }
  }

  return { value: raw, hasValue: true }
}

function getAgentCorrelationId(sessionId: string): string {
  if (sessionId.startsWith("agent/")) {
    return sessionId
  }
  return `agent/${sessionId}`
}

function AgentSessionCard({
  session,
  onReviewApprovals,
}: {
  session: AgentSessionWithStatus
  onReviewApprovals?: (session: AgentSessionWithStatus) => void
}) {
  const workspaceId = useWorkspaceId()
  const createdAt = new Date(session.created_at)
  const correlationId = getAgentCorrelationId(session.id)
  const [expandedApprovals, setExpandedApprovals] = useState<Set<string>>(
    new Set()
  )

  const toggleApprovalExpanded = (approvalId: string) => {
    setExpandedApprovals((prev) => {
      const next = new Set(prev)
      if (next.has(approvalId)) {
        next.delete(approvalId)
      } else {
        next.add(approvalId)
      }
      return next
    })
  }
  const humanizeActionRef = (ref: string): string =>
    ref
      .replace(/[_-]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/\b\w/g, (char) => char.toUpperCase())
  const pendingApprovals =
    session.pendingApprovalCount > 0
      ? (session.approvals?.filter(
          (approval) => approval.status === "pending"
        ) ?? [])
      : []

  const completedApprovals =
    session.approvals?.filter((approval) => approval.status !== "pending") ?? []
  const sortedCompletedApprovals = [...completedApprovals].sort((a, b) => {
    const aTime = a.approved_at ?? a.created_at
    const bTime = b.approved_at ?? b.created_at
    return new Date(bTime ?? 0).getTime() - new Date(aTime ?? 0).getTime()
  })

  const formatWorkflowLabel = (
    summary?: AgentSessionWithStatus["parent_workflow"]
  ): JSX.Element => {
    if (!summary) return <span>Unknown workflow</span>
    return (
      <span className="flex items-center gap-1.5">
        <span>{summary.title}</span>
        {summary.alias && (
          <Badge variant="secondary" className="text-[10px] px-1.5 py-0">
            {summary.alias}
          </Badge>
        )}
      </span>
    )
  }

  const parentWorkflowLabel = session.parent_workflow ? (
    formatWorkflowLabel(session.parent_workflow)
  ) : (
    <span>Unknown workflow</span>
  )

  let rootWorkflowSummary = session.root_workflow ?? null
  if (
    rootWorkflowSummary &&
    session.parent_workflow &&
    rootWorkflowSummary.id === session.parent_workflow.id
  ) {
    rootWorkflowSummary = null
  }
  const rootWorkflowLabel = rootWorkflowSummary
    ? formatWorkflowLabel(rootWorkflowSummary)
    : null

  const sessionActionLabel =
    session.action_title ??
    (session.action_ref ? humanizeActionRef(session.action_ref) : null)

  const workflowLinks = [
    {
      label: "Workflow",
      formattedLabel: parentWorkflowLabel,
      workflowId: session.parent_id,
      executionId: session.parent_run_id,
    },
    // Only include root workflow if it's different from parent workflow
    ...(rootWorkflowSummary
      ? [
          {
            label: "Root workflow",
            formattedLabel: rootWorkflowLabel,
            workflowId: session.root_id,
            executionId: session.root_run_id,
          },
        ]
      : []),
  ]
    .filter(
      (
        item
      ): item is {
        label: string
        formattedLabel: JSX.Element | null
        workflowId: string
        executionId: string
      } => Boolean(item.workflowId && item.executionId)
    )
    .map(({ label, formattedLabel, executionId: fullExecutionId }) => {
      const { wf, exec } = splitExecutionId(fullExecutionId)
      return {
        label,
        formattedLabel,
        href: `/workspaces/${workspaceId}/workflows/${encodeURIComponent(wf)}/executions/${encodeURIComponent(exec)}`,
        tooltip: `${wf}/${exec}`,
      }
    })

  return (
    <div className="rounded-xl border border-border/60 bg-card px-4 py-3 shadow-sm transition hover:border-border">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-2">
            <StatusIndicator tone={session.statusTone} className="size-2.5" />
            <span className="text-sm font-semibold">
              {sessionActionLabel ?? `Session ${session.id.slice(0, 8)}`}
            </span>
          </div>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{createdAt.toLocaleString()}</span>
            <span aria-hidden="true">•</span>
            <span>{shortTimeAgo(createdAt)}</span>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span className="flex items-center gap-1">
            <span className="text-foreground/65">Session ID:</span>
            <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px]">
              {session.id}
            </code>
            <CopyButton
              value={correlationId}
              toastMessage="Copied tracecat correlation ID"
              tooltipMessage="Copy tracecat correlation ID (agent/<session_id>)"
            />
          </span>
          {session.pendingApprovalCount > 0 && (
            <span
              className={cn(
                "rounded-full px-2 py-0.5 text-xs font-medium",
                toneBadgeClasses[session.statusTone]
              )}
            >
              {session.pendingApprovalCount} pending approval
              {session.pendingApprovalCount > 1 ? "s" : ""}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          {workflowLinks.map((detail, index) => (
            <span key={detail.label} className="flex items-center gap-2">
              {index > 0 && <span aria-hidden="true">•</span>}
              <Link
                href={detail.href}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5"
                title={detail.label}
              >
                <WorkflowIcon className="size-3.5 text-foreground/50" />
                {detail.formattedLabel}
                <ExternalLinkIcon className="size-3" />
              </Link>
            </span>
          ))}
          {sessionActionLabel ? (
            <>
              <span aria-hidden="true">•</span>
              <span className="inline-flex items-center gap-1.5">
                <BoxIcon className="size-3.5 text-foreground/50" />
                {sessionActionLabel}
              </span>
            </>
          ) : null}
        </div>
        {sortedCompletedApprovals.length > 0 && (
          <div className="flex flex-col gap-1 pt-1">
            <span className="text-xs font-semibold text-muted-foreground">
              Recent decisions
            </span>
            <div className="flex flex-col gap-1.5">
              {sortedCompletedApprovals.map((approval) => {
                const approverUserId = approval.approved_by ?? null
                const decisionPayload = approval.decision as unknown
                const decisionKind =
                  typeof decisionPayload === "object" &&
                  decisionPayload !== null &&
                  "kind" in decisionPayload &&
                  typeof (decisionPayload as { kind?: unknown }).kind ===
                    "string"
                    ? (decisionPayload as { kind: string }).kind
                    : null
                const hasOverrideArgs =
                  decisionKind === "tool-approved" &&
                  typeof decisionPayload === "object" &&
                  decisionPayload !== null &&
                  "override_args" in decisionPayload &&
                  (decisionPayload as { override_args?: unknown })
                    .override_args !== undefined
                const decisionLabel =
                  approval.status === "approved"
                    ? hasOverrideArgs
                      ? "Approved with overrides"
                      : "Approved"
                    : "Rejected"
                const decisionTime = approval.approved_at
                  ? shortTimeAgo(new Date(approval.approved_at))
                  : approval.created_at
                    ? shortTimeAgo(new Date(approval.created_at))
                    : null
                const reasonText =
                  approval.reason && approval.reason.length > 0
                    ? ` — ${approval.reason}`
                    : ""
                const actionTypeKey = approval.tool_name
                  ? reconstructActionType(approval.tool_name)
                  : "unknown"
                const actionLabel = approval.tool_name
                  ? actionTypeKey
                  : "Unknown action"
                const { value: toolArgsValue, hasValue: hasToolArgs } =
                  normalizePayload(approval.tool_call_args)
                const { value: decisionValue, hasValue: hasDecisionValue } =
                  normalizePayload(decisionPayload)
                const hasExpandableContent = hasToolArgs || hasDecisionValue

                return (
                  <button
                    type="button"
                    key={approval.id}
                    onClick={() => {
                      if (hasExpandableContent) {
                        toggleApprovalExpanded(approval.id)
                      }
                    }}
                    className="w-full text-left flex items-start gap-2 rounded-md border border-border/50 bg-muted/20 px-2 py-1.5 transition-colors hover:bg-muted/30 disabled:opacity-50"
                    disabled={!hasExpandableContent}
                  >
                    <div className="flex size-7 items-center justify-center rounded-full bg-muted text-[11px] text-muted-foreground">
                      {approverUserId ? "✓" : "?"}
                    </div>
                    <div className="flex min-w-0 w-full flex-col gap-0.5">
                      <div className="flex min-w-0 items-start justify-between gap-2">
                        <div className="flex min-w-0 items-center gap-2">
                          {getIcon(actionTypeKey, {
                            className: "size-5 shrink-0",
                          })}
                          <span className="truncate text-xs font-medium text-foreground">
                            {actionLabel}
                          </span>
                        </div>
                        {hasExpandableContent ? (
                          <Collapsible
                            open={expandedApprovals.has(approval.id)}
                            onOpenChange={() =>
                              toggleApprovalExpanded(approval.id)
                            }
                          >
                            <CollapsibleTrigger asChild>
                              <span
                                onClick={(e) => e.stopPropagation()}
                                className="flex shrink-0 text-[11px] font-medium text-muted-foreground/80 hover:text-muted-foreground transition-colors"
                              >
                                <ChevronDownIcon className="size-3.5 transition-transform data-[state=open]:rotate-180" />
                              </span>
                            </CollapsibleTrigger>
                          </Collapsible>
                        ) : null}
                      </div>
                      <span className="text-[11px] text-muted-foreground">
                        {decisionLabel}
                        {hasOverrideArgs ? " • Overrides applied" : ""}
                        {approverUserId ? " by user" : ""}
                        {decisionTime ? ` • ${decisionTime}` : ""}
                        {reasonText}
                      </span>
                      {hasExpandableContent ? (
                        <Collapsible
                          open={expandedApprovals.has(approval.id)}
                          onOpenChange={() =>
                            toggleApprovalExpanded(approval.id)
                          }
                        >
                          <CollapsibleContent
                            onClick={(e) => e.stopPropagation()}
                          >
                            <div className="mt-2 flex flex-col gap-3 text-xs">
                              {hasToolArgs ? (
                                <div className="flex flex-col gap-1.5">
                                  <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                                    Original args
                                  </span>
                                  <JsonViewWithControls
                                    src={toolArgsValue}
                                    defaultExpanded={true}
                                    defaultTab="nested"
                                    showControls={false}
                                    className="text-xs"
                                  />
                                </div>
                              ) : null}
                              {hasDecisionValue ? (
                                <div className="flex flex-col gap-1.5">
                                  <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground">
                                    {approval.status === "approved"
                                      ? hasOverrideArgs
                                        ? "Override payload"
                                        : "Decision payload"
                                      : "Rejection payload"}
                                  </span>
                                  <JsonViewWithControls
                                    src={decisionValue}
                                    defaultExpanded={true}
                                    defaultTab="nested"
                                    showControls={false}
                                    className="text-xs"
                                  />
                                </div>
                              ) : null}
                            </div>
                          </CollapsibleContent>
                        </Collapsible>
                      ) : null}
                    </div>
                  </button>
                )
              })}
            </div>
          </div>
        )}
        {pendingApprovals.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-2 text-[11px] text-amber-700">
            <div className="flex flex-wrap gap-2">
              {pendingApprovals.map((approval: ApprovalRead) => {
                const actionTypeKey = approval.tool_name
                  ? reconstructActionType(approval.tool_name)
                  : "unknown"
                const toolLabel = approval.tool_name
                  ? actionTypeKey
                  : "Unknown tool"
                return (
                  <span
                    key={approval.id}
                    className="flex items-center gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2 py-0.5 font-medium"
                  >
                    {getIcon(actionTypeKey, {
                      className: "size-5 shrink-0",
                    })}
                    <span>Awaiting {toolLabel}</span>
                  </span>
                )
              })}
            </div>
            <Button
              size="sm"
              variant="secondary"
              onClick={() => onReviewApprovals?.(session)}
              className="shrink-0"
            >
              Review approvals
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// AgentsDashboard — catalog view with folder navigation
// ---------------------------------------------------------------------------

type AgentsSortField = "updated_at" | "created_at" | "name"
type AgentsSortDirection = "asc" | "desc"
type AgentsSortValue = {
  field: AgentsSortField
  direction: AgentsSortDirection
}
type AgentsViewMode = "folders" | "list"

const DEFAULT_AGENT_SORT: AgentsSortValue = {
  field: "updated_at",
  direction: "desc",
}

enum AgentActiveDialog {
  FolderCreate,
  FolderRename,
  FolderDelete,
  PresetMove,
  PresetDelete,
}

const ROW_NAME_COLUMN_CLASS = "min-w-0 w-[340px] shrink-0 truncate text-xs"

function parseAgentsViewMode(value: string | null): AgentsViewMode {
  return value === "list" ? "list" : "folders"
}

function normalizeAgentFolderPath(rawPath: string | null): string {
  if (!rawPath || rawPath === "/") {
    return "/"
  }
  const withLeadingSlash = rawPath.startsWith("/") ? rawPath : `/${rawPath}`
  return withLeadingSlash.endsWith("/") && withLeadingSlash !== "/"
    ? withLeadingSlash.slice(0, -1)
    : withLeadingSlash
}

function getAgentRelativeDateLabel(dateValue: string): string {
  const timestamp = new Date(dateValue).getTime()
  if (Number.isNaN(timestamp)) {
    return "0m"
  }
  const diffMs = Math.max(0, Date.now() - timestamp)
  const minuteMs = 60_000
  const hourMs = 60 * minuteMs
  const dayMs = 24 * hourMs
  const monthMs = 30 * dayMs
  const yearMs = 365 * dayMs

  if (diffMs < hourMs) {
    return `${Math.max(1, Math.floor(diffMs / minuteMs))}m`
  }
  if (diffMs < dayMs) {
    return `${Math.max(1, Math.floor(diffMs / hourMs))}hr`
  }
  if (diffMs < monthMs) {
    return `${Math.max(1, Math.floor(diffMs / dayMs))}d`
  }
  if (diffMs < yearMs) {
    return `${Math.max(1, Math.floor(diffMs / monthMs))}mo`
  }
  return `${Math.max(1, Math.floor(diffMs / yearMs))}y`
}

function getAgentItemName(item: AgentDirectoryItem): string {
  return item.name
}

function compareAgentItemsBySort(
  a: AgentDirectoryItem,
  b: AgentDirectoryItem,
  sortBy: AgentsSortValue
): number {
  // Keep folders pinned above presets regardless of sort.
  if (a.type !== b.type) {
    return a.type === "folder" ? -1 : 1
  }
  const direction = sortBy.direction === "asc" ? 1 : -1

  if (sortBy.field === "name") {
    return (
      getAgentItemName(a).localeCompare(getAgentItemName(b), undefined, {
        numeric: true,
        sensitivity: "base",
      }) * direction
    )
  }

  const aTimestamp = Date.parse(a[sortBy.field])
  const bTimestamp = Date.parse(b[sortBy.field])
  if (aTimestamp !== bTimestamp) {
    if (Number.isNaN(aTimestamp) && Number.isNaN(bTimestamp)) {
      return 0
    }
    if (Number.isNaN(aTimestamp)) {
      return 1
    }
    if (Number.isNaN(bTimestamp)) {
      return -1
    }
    return (aTimestamp - bTimestamp) * direction
  }
  return (
    getAgentItemName(a).localeCompare(getAgentItemName(b), undefined, {
      numeric: true,
      sensitivity: "base",
    }) * direction
  )
}

function AgentPresetTagPills({
  tags,
}: {
  tags?: Array<{ id: string; name: string; color?: string | null }> | null
}) {
  if (!tags || tags.length === 0) {
    return null
  }
  return (
    <div className="flex min-w-0 items-center gap-1">
      {tags.slice(0, 3).map((tag) => (
        <span
          key={tag.id}
          className={cn(
            "inline-flex h-5 max-w-[110px] items-center truncate rounded-full px-2 text-[10px] font-medium",
            !tag.color && "bg-muted text-muted-foreground"
          )}
          style={
            tag.color
              ? {
                  backgroundColor: `${tag.color}20`,
                  color: tag.color,
                }
              : undefined
          }
        >
          {tag.name}
        </span>
      ))}
      {tags.length > 3 && (
        <span className="text-[10px] text-muted-foreground">
          +{tags.length - 3}
        </span>
      )}
    </div>
  )
}

// -- Inline dialogs ----------------------------------------------------------

export function AgentFolderCreateDialog({
  open,
  onOpenChange,
  currentPath,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentPath: string
}) {
  const workspaceId = useWorkspaceId()
  const { createFolder, createFolderIsPending } = useAgentFolders(workspaceId, {
    enabled: open,
  })
  const [name, setName] = useState("")

  useEffect(() => {
    if (open) {
      setName("")
    }
  }, [open])

  const handleSubmit = async () => {
    if (!name.trim()) return
    try {
      await createFolder({
        name: name.trim(),
        parent_path: currentPath === "/" ? undefined : currentPath,
      })
      onOpenChange(false)
    } catch {
      // toast handled by hook
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Create folder</DialogTitle>
          <DialogDescription>
            Create a new folder to organize your agents.
          </DialogDescription>
        </DialogHeader>
        <div className="py-2">
          <Input
            placeholder="Folder name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                void handleSubmit()
              }
            }}
          />
        </div>
        <DialogFooter>
          <Button
            variant="secondary"
            onClick={() => onOpenChange(false)}
            disabled={createFolderIsPending}
          >
            Cancel
          </Button>
          <Button
            onClick={() => void handleSubmit()}
            disabled={createFolderIsPending || !name.trim()}
          >
            {createFolderIsPending ? <Spinner className="mr-2 size-4" /> : null}
            Create
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AgentFolderRenameDialog({
  open,
  onOpenChange,
  folder,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  folder: AgentFolderDirectoryItem | null
}) {
  const workspaceId = useWorkspaceId()
  const { updateFolder, updateFolderIsPending } = useAgentFolders(workspaceId, {
    enabled: open,
  })
  const [name, setName] = useState("")

  useEffect(() => {
    if (open && folder) {
      setName(folder.name)
    }
  }, [open, folder])

  const handleSubmit = async () => {
    if (!folder || !name.trim()) return
    try {
      await updateFolder({ folderId: folder.id, name: name.trim() })
      onOpenChange(false)
    } catch {
      // toast handled by hook
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(isOpen) => {
        onOpenChange(isOpen)
      }}
    >
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Rename folder</DialogTitle>
          <DialogDescription>
            Enter a new name for the folder.
          </DialogDescription>
        </DialogHeader>
        <div className="py-2">
          <Input
            placeholder="New folder name"
            value={name}
            onChange={(e) => setName(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") {
                void handleSubmit()
              }
            }}
          />
        </div>
        <DialogFooter>
          <Button
            variant="secondary"
            onClick={() => onOpenChange(false)}
            disabled={updateFolderIsPending}
          >
            Cancel
          </Button>
          <Button
            onClick={() => void handleSubmit()}
            disabled={updateFolderIsPending || !name.trim()}
          >
            {updateFolderIsPending ? (
              <Spinner className="mr-2 size-4" />
            ) : (
              <Pencil className="mr-2 size-4" />
            )}
            Rename
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AgentFolderDeleteDialog({
  open,
  onOpenChange,
  folder,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  folder: AgentFolderDirectoryItem | null
}) {
  const workspaceId = useWorkspaceId()
  const { deleteFolder } = useAgentFolders(workspaceId, { enabled: open })
  const [confirmName, setConfirmName] = useState("")

  useEffect(() => {
    if (open) {
      setConfirmName("")
    }
  }, [open])

  return (
    <AlertDialog
      open={open}
      onOpenChange={(isOpen) => {
        onOpenChange(isOpen)
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete folder</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete this folder? This action cannot be
            undone. You cannot delete a folder that contains agents or other
            folders.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="my-4">
          <Input
            placeholder={`Type "${folder?.name}" to confirm`}
            value={confirmName}
            onChange={(e) => setConfirmName(e.target.value)}
          />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={!folder || confirmName !== folder?.name}
            onClick={async () => {
              if (folder) {
                try {
                  await deleteFolder({ folderId: folder.id })
                } catch {
                  // toast handled by hook
                }
              }
            }}
          >
            Confirm
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function AgentPresetMoveDialog({
  open,
  onOpenChange,
  preset,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  preset: AgentPresetDirectoryItem | AgentPresetReadMinimal | null
}) {
  const workspaceId = useWorkspaceId()
  const { moveAgentPreset, moveAgentPresetIsPending } =
    useMoveAgentPreset(workspaceId)
  const { folders } = useAgentFolders(workspaceId, { enabled: open })
  const [selectedFolder, setSelectedFolder] = useState<string | null>(null)
  const [openFolderSelect, setOpenFolderSelect] = useState(false)

  useEffect(() => {
    if (open) {
      setSelectedFolder(null)
    }
  }, [open])

  const handleMove = async () => {
    if (!preset) return
    try {
      await moveAgentPreset({
        presetId: preset.id,
        folder_path: selectedFolder || "/",
      })
      onOpenChange(false)
    } catch {
      // toast handled by hook
    }
  }

  // getFileTreeItems expects WorkflowFolderRead[] but AgentFolderRead[] is structurally identical.
  const fileTreeItems = getFileTreeItems(folders as never)

  const handleSelectFolder = (path: string) => {
    setSelectedFolder(path)
    setOpenFolderSelect(false)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Move agent</DialogTitle>
          <DialogDescription>
            Choose a folder to move{" "}
            <span className="font-medium">{preset?.name}</span> to.
          </DialogDescription>
        </DialogHeader>
        <div className="flex w-full items-center py-4">
          <Popover open={openFolderSelect} onOpenChange={setOpenFolderSelect}>
            <PopoverTrigger asChild>
              <Button
                variant="outline"
                role="combobox"
                aria-expanded={openFolderSelect}
                className="flex w-96 max-w-full min-w-0 justify-between overflow-hidden"
              >
                {selectedFolder ? (
                  <div className="flex min-w-0 flex-1 items-center gap-2">
                    <FolderIcon className="size-4 shrink-0" />
                    <span
                      className="truncate"
                      title={
                        selectedFolder === "/"
                          ? ROOT_FOLDER_NAME
                          : selectedFolder
                      }
                    >
                      {selectedFolder === "/"
                        ? ROOT_FOLDER_NAME
                        : selectedFolder}
                    </span>
                  </div>
                ) : (
                  <div className="flex items-center gap-2">
                    Select a folder...
                  </div>
                )}
                <ChevronDownIcon className="ml-2 size-4 shrink-0 opacity-50" />
              </Button>
            </PopoverTrigger>
            <PopoverContent className="w-[--radix-popover-trigger-width] overflow-hidden p-0">
              <FileTreeCommand
                items={fileTreeItems}
                onSelect={handleSelectFolder}
              />
            </PopoverContent>
          </Popover>
        </div>
        <DialogFooter className="sm:justify-end">
          <Button
            variant="secondary"
            onClick={() => onOpenChange(false)}
            disabled={moveAgentPresetIsPending}
          >
            Cancel
          </Button>
          <Button
            onClick={() => void handleMove()}
            disabled={moveAgentPresetIsPending || !selectedFolder}
          >
            {moveAgentPresetIsPending ? "Moving..." : "Move"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function AgentPresetDeleteDialog({
  open,
  onOpenChange,
  preset,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  preset: AgentPresetDirectoryItem | AgentPresetReadMinimal | null
}) {
  const workspaceId = useWorkspaceId()
  const { deleteAgentPreset } = useDeleteAgentPreset(workspaceId)
  const [confirmName, setConfirmName] = useState("")

  useEffect(() => {
    if (open) {
      setConfirmName("")
    }
  }, [open])

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete agent</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to delete this agent preset? This action
            cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="my-4">
          <Input
            placeholder={`Type "${preset?.name}" to confirm`}
            value={confirmName}
            onChange={(e) => setConfirmName(e.target.value)}
          />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={!preset || confirmName !== preset?.name}
            onClick={async () => {
              if (preset) {
                try {
                  await deleteAgentPreset({
                    presetId: preset.id,
                    presetName: preset.name,
                  })
                } catch {
                  // toast handled by hook
                }
              }
            }}
          >
            Confirm
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

// -- Context menu actions ----------------------------------------------------

function AgentFolderContextActions({
  item,
  setActiveDialog,
  setSelectedFolder,
}: {
  item: AgentFolderDirectoryItem
  setActiveDialog: (dialog: AgentActiveDialog | null) => void
  setSelectedFolder: (folder: AgentFolderDirectoryItem | null) => void
}) {
  return (
    <ContextMenuGroup>
      <ContextMenuItem
        className="text-xs"
        onClick={(e) => e.stopPropagation()}
        onSelect={(e) => {
          e.stopPropagation()
          navigator.clipboard.writeText(item.id)
          toast({ title: "Copied", description: "Folder ID copied" })
        }}
      >
        <Copy className="mr-2 size-3.5" />
        Copy folder ID
      </ContextMenuItem>
      <ContextMenuItem
        className="text-xs"
        onClick={(e) => e.stopPropagation()}
        onSelect={(e) => {
          e.stopPropagation()
          setSelectedFolder(item)
          setActiveDialog(AgentActiveDialog.FolderRename)
        }}
      >
        <Pencil className="mr-2 size-3.5" />
        Rename folder
      </ContextMenuItem>
      <ContextMenuSeparator />
      <ContextMenuItem
        className="text-xs text-rose-500 focus:text-rose-600"
        onClick={(e) => e.stopPropagation()}
        onSelect={(e) => {
          e.stopPropagation()
          setSelectedFolder(item)
          setActiveDialog(AgentActiveDialog.FolderDelete)
        }}
      >
        <Trash2 className="mr-2 size-3.5" />
        Delete folder
      </ContextMenuItem>
    </ContextMenuGroup>
  )
}

function AgentPresetContextActions({
  item,
  setActiveDialog,
  setSelectedPreset,
  availableTags,
  areTagsLoading = false,
  onDuplicate,
  duplicateDisabled = false,
}: {
  item: AgentPresetDirectoryItem | AgentPresetReadMinimal
  setActiveDialog: (dialog: AgentActiveDialog | null) => void
  setSelectedPreset: (
    preset: AgentPresetDirectoryItem | AgentPresetReadMinimal | null
  ) => void
  availableTags?: AgentTagRead[]
  areTagsLoading?: boolean
  onDuplicate?: (
    item: AgentPresetDirectoryItem | AgentPresetReadMinimal
  ) => void
  duplicateDisabled?: boolean
}) {
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()
  return (
    <ContextMenuGroup>
      <ContextMenuItem
        className="text-xs"
        onClick={(e) => e.stopPropagation()}
        asChild
      >
        <Link
          href={`/workspaces/${workspaceId}/agents/${item.id}`}
          target="_blank"
          rel="noopener noreferrer"
        >
          <ExternalLinkIcon className="mr-2 size-3.5" />
          Open in new tab
        </Link>
      </ContextMenuItem>
      <ContextMenuItem
        className="text-xs"
        onClick={(e) => e.stopPropagation()}
        onSelect={(e) => {
          e.stopPropagation()
          setSelectedPreset(item)
          setActiveDialog(AgentActiveDialog.PresetMove)
        }}
      >
        <FolderKanban className="mr-2 size-3.5" />
        Move to folder
      </ContextMenuItem>
      {availableTags && availableTags.length > 0 ? (
        <ContextMenuSub>
          <ContextMenuSubTrigger
            className="text-xs"
            onClick={(e) => e.stopPropagation()}
          >
            <TagsIcon className="mr-2 size-3.5" />
            Tags
          </ContextMenuSubTrigger>
          <ContextMenuPortal>
            <ContextMenuSubContent>
              {availableTags.map((tag) => {
                const hasTag = item.tags?.some((t) => t.id === tag.id)
                return (
                  <ContextMenuCheckboxItem
                    key={tag.id}
                    className="text-xs"
                    checked={hasTag}
                    onClick={async (e) => {
                      e.stopPropagation()
                      try {
                        if (hasTag) {
                          await agentPresetsRemovePresetTag({
                            presetId: item.id,
                            tagId: tag.id,
                            workspaceId,
                          })
                          toast({
                            title: "Tag removed",
                            description: `Removed tag "${tag.name}" from agent`,
                          })
                        } else {
                          await agentPresetsAddPresetTag({
                            presetId: item.id,
                            workspaceId,
                            requestBody: { tag_id: tag.id },
                          })
                          toast({
                            title: "Tag added",
                            description: `Added tag "${tag.name}" to agent`,
                          })
                        }
                        await Promise.all([
                          queryClient.invalidateQueries({
                            queryKey: ["agent-presets", workspaceId],
                          }),
                          queryClient.invalidateQueries({
                            queryKey: ["agent-directory-items", workspaceId],
                          }),
                          queryClient.invalidateQueries({
                            queryKey: ["agent-preset", workspaceId, item.id],
                          }),
                        ])
                      } catch (error) {
                        console.error("Failed to modify tag:", error)
                        toast({
                          title: "Error",
                          description: `Failed to ${hasTag ? "remove" : "add"} tag`,
                          variant: "destructive",
                        })
                      }
                    }}
                  >
                    <div
                      className="mr-2 flex size-2 rounded-full"
                      style={{
                        backgroundColor: tag.color || undefined,
                      }}
                    />
                    <span>{tag.name}</span>
                  </ContextMenuCheckboxItem>
                )
              })}
            </ContextMenuSubContent>
          </ContextMenuPortal>
        </ContextMenuSub>
      ) : areTagsLoading ? (
        <ContextMenuItem
          className="!bg-transparent text-xs !text-muted-foreground"
          onClick={(e) => e.stopPropagation()}
        >
          <TagsIcon className="mr-2 size-3.5" />
          <span>Loading tags...</span>
        </ContextMenuItem>
      ) : (
        <ContextMenuItem
          className="!bg-transparent text-xs !text-muted-foreground hover:cursor-not-allowed"
          onClick={(e) => e.stopPropagation()}
        >
          <TagsIcon className="mr-2 size-3.5" />
          <span>No tags available</span>
        </ContextMenuItem>
      )}
      <ContextMenuItem
        className="text-xs"
        onClick={(e) => e.stopPropagation()}
        onSelect={(e) => {
          e.stopPropagation()
          navigator.clipboard.writeText(item.id)
          toast({ title: "Copied", description: "Agent ID copied" })
        }}
      >
        <Copy className="mr-2 size-3.5" />
        Copy agent ID
      </ContextMenuItem>
      {onDuplicate && (
        <ContextMenuItem
          className="text-xs"
          disabled={duplicateDisabled}
          onClick={(e) => {
            e.stopPropagation()
            onDuplicate(item)
          }}
        >
          <CopyPlus className="mr-2 size-3.5" />
          Duplicate agent
        </ContextMenuItem>
      )}
      <ContextMenuSeparator />
      <ContextMenuItem
        className="text-xs text-rose-500 focus:text-rose-600"
        onClick={(e) => e.stopPropagation()}
        onSelect={(e) => {
          e.stopPropagation()
          setSelectedPreset(item)
          setActiveDialog(AgentActiveDialog.PresetDelete)
        }}
      >
        <Trash2 className="mr-2 size-3.5" />
        Delete agent
      </ContextMenuItem>
    </ContextMenuGroup>
  )
}

// -- Row components ----------------------------------------------------------

function AgentCatalogRow({
  item,
  onOpenPreset,
  onOpenFolder,
  setSelectedPreset,
  setSelectedFolder,
  setActiveDialog,
  availableTags,
  areTagsLoading,
  onDuplicate,
  duplicateDisabled,
}: {
  item: AgentDirectoryItem
  onOpenPreset: (presetId: string) => void
  onOpenFolder: (path: string) => void
  setSelectedPreset: (
    preset: AgentPresetDirectoryItem | AgentPresetReadMinimal | null
  ) => void
  setSelectedFolder: (folder: AgentFolderDirectoryItem | null) => void
  setActiveDialog: (dialog: AgentActiveDialog | null) => void
  availableTags?: AgentTagRead[]
  areTagsLoading?: boolean
  onDuplicate?: (
    item: AgentPresetDirectoryItem | AgentPresetReadMinimal
  ) => void
  duplicateDisabled?: boolean
}) {
  const [isContextMenuOpen, setIsContextMenuOpen] = useState(false)

  if (item.type === "folder") {
    const itemCountLabel = item.num_items === 1 ? "item" : "items"

    return (
      <ContextMenu onOpenChange={setIsContextMenuOpen}>
        <ContextMenuTrigger asChild>
          <div
            className={cn(
              "group/item flex items-center gap-2 px-4 py-2 transition-colors hover:bg-muted/50",
              isContextMenuOpen && "bg-muted/70"
            )}
          >
            <button
              type="button"
              onClick={() => onOpenFolder(item.path)}
              className="flex min-w-0 flex-1 items-center gap-3 bg-transparent p-0 text-left"
            >
              <FolderIcon className="size-4 shrink-0 text-black" />
              <div className="flex min-w-0 flex-1 items-center gap-3">
                <span className={ROW_NAME_COLUMN_CLASS}>{item.name}</span>
                <div className="flex shrink-0 items-center gap-1">
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Badge
                        variant="secondary"
                        className="h-5 cursor-default px-2 text-[10px] font-normal"
                      >
                        <Clock3 className="mr-1 size-3" />
                        {getAgentRelativeDateLabel(item.updated_at)}
                      </Badge>
                    </TooltipTrigger>
                    <TooltipContent>
                      {format(new Date(item.updated_at), "PPpp")}
                    </TooltipContent>
                  </Tooltip>
                  <Badge
                    variant="secondary"
                    className="h-5 px-2 text-[10px] font-normal"
                  >
                    <BoxIcon className="mr-1 size-3" />
                    {item.num_items} {itemCountLabel}
                  </Badge>
                </div>
              </div>
            </button>
          </div>
        </ContextMenuTrigger>
        <ContextMenuContent className="w-48">
          <AgentFolderContextActions
            item={item}
            setActiveDialog={setActiveDialog}
            setSelectedFolder={setSelectedFolder}
          />
        </ContextMenuContent>
      </ContextMenu>
    )
  }

  // Preset row
  return (
    <ContextMenu onOpenChange={setIsContextMenuOpen}>
      <ContextMenuTrigger asChild>
        <div
          className={cn(
            "group/item flex items-center gap-2 px-4 py-2 transition-colors hover:bg-muted/50",
            isContextMenuOpen && "bg-muted/70"
          )}
        >
          <button
            type="button"
            onClick={() => onOpenPreset(item.id)}
            className="flex min-w-0 flex-1 items-center gap-3 bg-transparent p-0 text-left"
          >
            <MousePointerClickIcon className="size-4 shrink-0 text-primary" />
            <div className="flex min-w-0 flex-1 items-center gap-3">
              <span className={ROW_NAME_COLUMN_CLASS}>{item.name}</span>
              <div className="flex min-w-0 flex-1 items-center justify-start gap-2 overflow-hidden">
                <div className="flex shrink-0 items-center gap-1">
                  <Badge
                    variant="secondary"
                    className="h-5 px-2 text-[10px] font-normal"
                  >
                    {item.model_provider}
                  </Badge>
                  <Badge
                    variant="secondary"
                    className="h-5 px-2 text-[10px] font-normal"
                  >
                    {item.model_name}
                  </Badge>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Badge
                        variant="secondary"
                        className="h-5 cursor-default px-2 text-[10px] font-normal"
                      >
                        <Clock3 className="mr-1 size-3" />
                        {getAgentRelativeDateLabel(item.updated_at)}
                      </Badge>
                    </TooltipTrigger>
                    <TooltipContent>
                      {format(new Date(item.updated_at), "PPpp")}
                    </TooltipContent>
                  </Tooltip>
                </div>
                <AgentPresetTagPills tags={item.tags} />
              </div>
            </div>
          </button>
        </div>
      </ContextMenuTrigger>
      <ContextMenuContent className="w-52">
        <AgentPresetContextActions
          item={item}
          setActiveDialog={setActiveDialog}
          setSelectedPreset={setSelectedPreset}
          availableTags={availableTags}
          areTagsLoading={areTagsLoading}
          onDuplicate={onDuplicate}
          duplicateDisabled={duplicateDisabled}
        />
      </ContextMenuContent>
    </ContextMenu>
  )
}

// -- Header ------------------------------------------------------------------

const SORT_FIELD_OPTIONS = [
  { value: "updated_at", label: "Updated", icon: Clock3 },
  { value: "created_at", label: "Created", icon: Calendar },
  { value: "name", label: "Name", icon: Type },
] as const

const VIEW_ICON: Record<AgentsViewMode, React.ReactNode> = {
  list: <ListIcon className="size-3.5 text-muted-foreground" />,
  folders: <FolderIcon className="size-3.5 text-muted-foreground" />,
}

const VIEW_LABEL: Record<AgentsViewMode, string> = {
  list: "List",
  folders: "Folders",
}

function AgentsCatalogHeader({
  searchQuery,
  onSearchChange,
  sortBy,
  onSortByChange,
  view,
  onViewChange,
  totalCount,
}: {
  searchQuery: string
  onSearchChange: (query: string) => void
  sortBy: AgentsSortValue
  onSortByChange: (sort: AgentsSortValue) => void
  view: AgentsViewMode
  onViewChange: (view: AgentsViewMode) => void
  totalCount: number
}) {
  const selectedSortLabel =
    SORT_FIELD_OPTIONS.find((o) => o.value === sortBy.field)?.label ?? "Updated"

  return (
    <div className="shrink-0 border-b">
      <header className="flex h-10 items-center border-b pl-3 pr-4">
        <div className="flex min-w-0 items-center gap-3">
          <div className="flex h-7 w-7 shrink-0 items-center justify-center">
            <SearchIcon className="size-4 text-muted-foreground" />
          </div>
          <Input
            type="text"
            placeholder="Search agents..."
            value={searchQuery}
            onChange={(e) => onSearchChange(e.target.value)}
            className={cn(
              "h-7 w-56 border-none bg-transparent p-0",
              "text-sm",
              "shadow-none outline-none",
              "placeholder:text-muted-foreground",
              "focus-visible:ring-0 focus-visible:ring-offset-0"
            )}
          />
        </div>
        <div className="ml-auto flex items-center gap-2">
          <span className="text-xs text-muted-foreground">
            {totalCount} agents
          </span>
        </div>
      </header>

      <div className="flex flex-wrap items-center gap-2 px-4 py-2">
        <Select
          value={view}
          onValueChange={(v) => onViewChange(v as AgentsViewMode)}
        >
          <SelectTrigger className="h-6 w-[138px] rounded-md px-2 text-xs font-medium">
            <div className="flex items-center gap-1.5">
              {VIEW_ICON[view]}
              <span>View</span>
            </div>
            <SelectValue placeholder={VIEW_LABEL[view]} />
          </SelectTrigger>
          <SelectContent align="start">
            <SelectItem value="list">List</SelectItem>
            <SelectItem value="folders">Folders</SelectItem>
          </SelectContent>
        </Select>

        <div className="inline-flex items-center rounded-md border border-input bg-transparent">
          <Select
            value={sortBy.field}
            onValueChange={(nextField) =>
              onSortByChange({
                field: nextField as AgentsSortField,
                direction: sortBy.direction,
              })
            }
          >
            <SelectTrigger className="h-6 w-[145px] rounded-r-none border-0 bg-transparent px-2 text-xs font-medium shadow-none focus:ring-0">
              <span className="text-muted-foreground">Sort by</span>
              <span className="text-foreground">{selectedSortLabel}</span>
            </SelectTrigger>
            <SelectContent align="start">
              {SORT_FIELD_OPTIONS.map((option) => {
                const Icon = option.icon
                return (
                  <SelectItem key={option.value} value={option.value}>
                    <span className="flex items-center gap-2">
                      <Icon className="size-3.5 text-muted-foreground" />
                      <span>{option.label}</span>
                    </span>
                  </SelectItem>
                )
              })}
            </SelectContent>
          </Select>
          <button
            type="button"
            className="flex h-6 w-7 items-center justify-center border-l border-input text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground"
            onClick={() =>
              onSortByChange({
                field: sortBy.field,
                direction: sortBy.direction === "asc" ? "desc" : "asc",
              })
            }
            aria-label={
              sortBy.direction === "asc" ? "Sort ascending" : "Sort descending"
            }
          >
            {sortBy.direction === "asc" ? (
              <ArrowUpIcon className="size-3.5" />
            ) : (
              <ArrowDownIcon className="size-3.5" />
            )}
          </button>
        </div>
      </div>
    </div>
  )
}

// -- Main component ----------------------------------------------------------

/** Self-contained agents catalog view used by the agents page. */
export function AgentsDashboard() {
  const router = useRouter()
  const queryClient = useQueryClient()
  const workspaceId = useWorkspaceId()
  const searchParams = useSearchParams()

  const view = parseAgentsViewMode(searchParams?.get("view"))
  const currentPath = normalizeAgentFolderPath(searchParams?.get("path"))

  const [searchQuery, setSearchQuery] = useState("")
  const [sortBy, setSortBy] = useState<AgentsSortValue>(DEFAULT_AGENT_SORT)

  const [activeDialog, setActiveDialog] = useState<AgentActiveDialog | null>(
    null
  )
  const [selectedFolder, setSelectedFolder] =
    useState<AgentFolderDirectoryItem | null>(null)
  const [selectedPreset, setSelectedPreset] = useState<
    AgentPresetDirectoryItem | AgentPresetReadMinimal | null
  >(null)

  // Data hooks
  const { presets, presetsIsLoading, presetsError } = useAgentPresets(
    workspaceId,
    { enabled: view === "list" }
  )
  const { directoryItems, directoryItemsIsLoading, directoryItemsError } =
    useAgentDirectoryItems(currentPath, workspaceId, {
      enabled: view === "folders",
    })
  const { agentTags, agentTagsIsLoading } = useAgentTagCatalog(workspaceId)
  const { createAgentPreset, createAgentPresetIsPending } =
    useCreateAgentPreset(workspaceId)

  const handleDuplicatePreset = useCallback(
    async (item: AgentPresetDirectoryItem | AgentPresetReadMinimal) => {
      try {
        const [fullPreset, allPresets] = await Promise.all([
          queryClient.fetchQuery({
            queryKey: ["agent-preset", workspaceId, item.id],
            queryFn: async () =>
              await agentPresetsGetAgentPreset({
                workspaceId,
                presetId: item.id,
              }),
          }),
          presets
            ? Promise.resolve(presets)
            : queryClient.fetchQuery({
                queryKey: ["agent-presets", workspaceId],
                queryFn: async () =>
                  await agentPresetsListAgentPresets({ workspaceId }),
              }),
        ])

        const existingSlugs = allPresets
          .map((preset) => preset.slug)
          .filter((slug): slug is string => typeof slug === "string")
        const payload = buildDuplicateAgentPresetPayload(
          fullPreset,
          existingSlugs
        )
        const created = await createAgentPreset(payload)
        router.push(`/workspaces/${workspaceId}/agents/${created.id}`)
      } catch (error) {
        console.error("Failed to duplicate agent preset:", error)
        toast({
          title: "Error",
          description: "Could not duplicate agent. Please try again.",
          variant: "destructive",
        })
      }
    },
    [createAgentPreset, presets, queryClient, router, workspaceId]
  )

  const baseRoute = `/workspaces/${workspaceId}/agents`

  const buildRoute = useCallback(
    (params: URLSearchParams): string => {
      const query = params.toString()
      return query ? `${baseRoute}?${query}` : baseRoute
    },
    [baseRoute]
  )

  const handleViewChange = useCallback(
    (nextView: AgentsViewMode) => {
      const nextParams = new URLSearchParams(searchParams?.toString() ?? "")
      nextParams.set("view", nextView)
      if (nextView === "list") {
        nextParams.delete("path")
      } else if (!nextParams.has("path")) {
        nextParams.set("path", "/")
      }
      router.replace(buildRoute(nextParams))
    },
    [buildRoute, router, searchParams]
  )

  const handleOpenFolder = useCallback(
    (path: string) => {
      const nextParams = new URLSearchParams(searchParams?.toString() ?? "")
      nextParams.set("view", "folders")
      nextParams.set("path", normalizeAgentFolderPath(path))
      router.push(buildRoute(nextParams))
    },
    [buildRoute, router, searchParams]
  )

  const handleOpenPreset = useCallback(
    (presetId: string) => {
      router.push(`/workspaces/${workspaceId}/agents/${presetId}`)
    },
    [router, workspaceId]
  )

  // Filtering and sorting
  const normalizedSearch = useMemo(
    () => searchQuery.trim().toLowerCase(),
    [searchQuery]
  )

  const matchesSearch = useCallback(
    (item: AgentDirectoryItem): boolean => {
      if (!normalizedSearch) return true
      const searchable =
        item.type === "folder"
          ? `${item.name} ${item.path}`
          : `${item.name} ${item.slug ?? ""} ${item.id}`
      return searchable.toLowerCase().includes(normalizedSearch)
    },
    [normalizedSearch]
  )

  // Folder view items
  const sortedDirectoryItems = useMemo(() => {
    const filtered = (directoryItems ?? []).filter(matchesSearch)
    return [...filtered].sort((a, b) => compareAgentItemsBySort(a, b, sortBy))
  }, [directoryItems, matchesSearch, sortBy])

  // List view items — convert presets to directory items
  const sortedListItems = useMemo(() => {
    const items: AgentDirectoryItem[] = (presets ?? []).map(
      (preset): AgentPresetDirectoryItem => ({
        type: "preset",
        id: preset.id,
        name: preset.name,
        slug: preset.slug,
        description: preset.description,
        model_provider: preset.model_provider,
        model_name: preset.model_name,
        folder_id: preset.folder_id ?? null,
        tags: preset.tags ?? [],
        created_at: preset.created_at,
        updated_at: preset.updated_at,
      })
    )
    const filtered = items.filter(matchesSearch)
    return [...filtered].sort((a, b) => compareAgentItemsBySort(a, b, sortBy))
  }, [presets, matchesSearch, sortBy])

  const visibleItems =
    view === "folders" ? sortedDirectoryItems : sortedListItems
  const isLoading =
    view === "folders" ? directoryItemsIsLoading : presetsIsLoading
  const error = view === "folders" ? directoryItemsError : presetsError

  return (
    <TooltipProvider>
      <div className="flex size-full flex-col overflow-hidden">
        <AgentsCatalogHeader
          searchQuery={searchQuery}
          onSearchChange={setSearchQuery}
          sortBy={sortBy}
          onSortByChange={setSortBy}
          view={view}
          onViewChange={handleViewChange}
          totalCount={visibleItems.length}
        />

        <div className="min-h-0 flex-1 overflow-auto">
          {isLoading ? (
            <div className="flex h-full items-center justify-center">
              <CenteredSpinner />
            </div>
          ) : error ? (
            <div className="flex h-full items-center justify-center px-6">
              <span className="text-sm text-destructive">
                Failed to load agents.
              </span>
            </div>
          ) : visibleItems.length === 0 ? (
            <div className="flex h-full p-6">
              <Empty>
                <EmptyHeader>
                  <EmptyMedia variant="icon">
                    <MousePointerClickIcon className="size-5 text-muted-foreground/60" />
                  </EmptyMedia>
                  <EmptyTitle>No agents yet</EmptyTitle>
                </EmptyHeader>
              </Empty>
            </div>
          ) : (
            <div className="divide-y">
              {visibleItems.map((item) => (
                <AgentCatalogRow
                  key={`${item.type}-${item.id}`}
                  item={item}
                  onOpenPreset={handleOpenPreset}
                  onOpenFolder={handleOpenFolder}
                  setSelectedPreset={setSelectedPreset}
                  setSelectedFolder={setSelectedFolder}
                  setActiveDialog={setActiveDialog}
                  availableTags={agentTags}
                  areTagsLoading={agentTagsIsLoading}
                  onDuplicate={handleDuplicatePreset}
                  duplicateDisabled={createAgentPresetIsPending}
                />
              ))}
            </div>
          )}
        </div>
      </div>

      {/* Dialogs */}
      <AgentFolderCreateDialog
        open={activeDialog === AgentActiveDialog.FolderCreate}
        onOpenChange={(isOpen) => {
          if (!isOpen) setActiveDialog(null)
        }}
        currentPath={currentPath}
      />
      <AgentFolderRenameDialog
        open={activeDialog === AgentActiveDialog.FolderRename}
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setActiveDialog(null)
            setSelectedFolder(null)
          }
        }}
        folder={selectedFolder}
      />
      <AgentFolderDeleteDialog
        open={activeDialog === AgentActiveDialog.FolderDelete}
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setActiveDialog(null)
            setSelectedFolder(null)
          }
        }}
        folder={selectedFolder}
      />
      <AgentPresetMoveDialog
        open={activeDialog === AgentActiveDialog.PresetMove}
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setActiveDialog(null)
            setSelectedPreset(null)
          }
        }}
        preset={selectedPreset}
      />
      <AgentPresetDeleteDialog
        open={activeDialog === AgentActiveDialog.PresetDelete}
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setActiveDialog(null)
            setSelectedPreset(null)
          }
        }}
        preset={selectedPreset}
      />
    </TooltipProvider>
  )
}
