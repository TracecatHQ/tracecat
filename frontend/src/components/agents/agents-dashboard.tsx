"use client"

import {
  AlertTriangleIcon,
  BoxIcon,
  ChevronDownIcon,
  ExternalLinkIcon,
  WorkflowIcon,
} from "lucide-react"
import Link from "next/link"
import { useMemo, useState } from "react"
import type { UserReadMinimal } from "@/client"
import { AgentApprovalsDialog } from "@/components/agents/agent-approvals-dialog"
import { CollapsibleSection } from "@/components/collapsible-section"
import { getIcon } from "@/components/icons"
import { JsonViewWithControls } from "@/components/json-viewer"
import { Spinner } from "@/components/loading/spinner"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import UserAvatar from "@/components/user-avatar"
import type { AgentSessionWithStatus, AgentStatusTone } from "@/lib/agents"
import {
  compareAgentStatusPriority,
  getAgentStatusMetadata,
} from "@/lib/agents"
import { User } from "@/lib/auth"
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
      <div className="flex h-full flex-col items-center justify-center gap-2 py-20 text-sm text-muted-foreground">
        <AlertTriangleIcon className="size-5 text-muted-foreground/60" />
        <p>No agent activity yet.</p>
        <p className="text-xs text-muted-foreground/70">
          When agents run, they will appear here grouped by their latest status.
        </p>
      </div>
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

function userReadMinimalToUser(user: UserReadMinimal): User {
  return new User({
    id: user.id,
    email: user.email,
    role: user.role,
    first_name: user.first_name ?? undefined,
    last_name: user.last_name ?? undefined,
    settings: {},
  })
}

function isEmptyObjectOrArray(value: unknown): boolean {
  if (Array.isArray(value)) {
    return value.length === 0
  }
  if (value && typeof value === "object") {
    return Object.keys(value as Record<string, unknown>).length === 0
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

function AgentSessionCard({
  session,
  onReviewApprovals,
}: {
  session: AgentSessionWithStatus
  onReviewApprovals?: (session: AgentSessionWithStatus) => void
}) {
  const workspaceId = useWorkspaceId()
  const createdAt = new Date(session.created_at)
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
    const aTime = a.approved_at ?? a.updated_at
    const bTime = b.approved_at ?? b.updated_at
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
                const approverUser = approval.approved_by
                  ? userReadMinimalToUser(approval.approved_by)
                  : undefined
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
                  : approval.updated_at
                    ? shortTimeAgo(new Date(approval.updated_at))
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
                    key={approval.id}
                    onClick={() => {
                      if (hasExpandableContent) {
                        toggleApprovalExpanded(approval.id)
                      }
                    }}
                    className="w-full text-left flex items-start gap-2 rounded-md border border-border/50 bg-muted/20 px-2 py-1.5 transition-colors hover:bg-muted/30 disabled:opacity-50"
                    disabled={!hasExpandableContent}
                  >
                    {approverUser ? (
                      <UserAvatar
                        user={approverUser}
                        alt={approverUser.getDisplayName()}
                        className="size-7"
                        fallbackClassName="bg-primary/10 text-primary text-xs"
                      />
                    ) : (
                      <div className="flex size-7 items-center justify-center rounded-full bg-muted text-[11px] text-muted-foreground">
                        ?
                      </div>
                    )}
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
                            <CollapsibleTrigger
                              onClick={(e) => e.stopPropagation()}
                              className="flex shrink-0 text-[11px] font-medium text-muted-foreground/80 hover:text-muted-foreground transition-colors"
                            >
                              <ChevronDownIcon className="size-3.5 transition-transform data-[state=open]:rotate-180" />
                            </CollapsibleTrigger>
                          </Collapsible>
                        ) : null}
                      </div>
                      <span className="text-[11px] text-muted-foreground">
                        {decisionLabel}
                        {hasOverrideArgs ? " • Overrides applied" : ""}
                        {approverUser
                          ? ` by ${approverUser.getDisplayName()}`
                          : approval.approved_by
                            ? " by unknown user"
                            : ""}
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
              {pendingApprovals.map((approval) => {
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
