"use client"

import type { JSONSchema7 } from "json-schema"
import {
  AlertTriangleIcon,
  BoxIcon,
  ChevronDownIcon,
  ExternalLinkIcon,
  Leaf,
  MoreHorizontal,
  WorkflowIcon,
} from "lucide-react"
import Link from "next/link"
import { useCallback, useEffect, useMemo, useState } from "react"
import type { UserReadMinimal } from "@/client"
import { agentSubmitAgentApprovals } from "@/client"
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Textarea } from "@/components/ui/textarea"
import { useToast } from "@/components/ui/use-toast"
import UserAvatar from "@/components/user-avatar"
import type {
  AgentApprovalDecisionPayload,
  AgentSessionWithStatus,
  AgentStatusTone,
} from "@/lib/agents"
import {
  compareAgentStatusPriority,
  getAgentStatusMetadata,
} from "@/lib/agents"
import { getRecommendationDisplay } from "@/lib/approval-recommendations"
import { User } from "@/lib/auth"
import type { TracecatApiError } from "@/lib/errors"
import { executionId as splitExecutionId } from "@/lib/event-history"
import { jsonSchemaToZod } from "@/lib/jsonschema"
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

const APPROVAL_VALUE_SCHEMA: JSONSchema7 = {
  oneOf: [
    { type: "boolean" },
    {
      type: "object",
      properties: {
        kind: {
          type: "string",
          enum: ["tool-approved"],
        },
        override_args: {
          type: "object",
          additionalProperties: true,
        },
      },
      required: ["kind"],
      additionalProperties: false,
    },
    {
      type: "object",
      properties: {
        kind: {
          type: "string",
          enum: ["tool-denied"],
        },
        message: {
          type: "string",
        },
      },
      required: ["kind"],
      additionalProperties: false,
    },
  ],
}

const approvalValueValidator = jsonSchemaToZod(APPROVAL_VALUE_SCHEMA)

type DecisionType = "approve" | "override" | "deny"

type DecisionFormState = {
  decision: DecisionType
  overrideArgs: string
  message: string
}

const createDefaultDecisionState = (): DecisionFormState => ({
  decision: "approve",
  overrideArgs: "",
  message: "",
})

const summarizeHistoryEntry = (entry: unknown): string => {
  if (typeof entry === "string") {
    return entry
  }
  if (entry && typeof entry === "object") {
    const maybeExecution = (entry as { execution?: unknown }).execution
    const maybeResult = (entry as { result?: unknown }).result

    const parts: string[] = []
    if (
      maybeExecution &&
      typeof maybeExecution === "object" &&
      maybeExecution !== null
    ) {
      const execution = maybeExecution as {
        run_id?: string
        status?: string
      }
      const execBits: string[] = []
      if (execution.run_id) {
        execBits.push(`Run ${execution.run_id}`)
      }
      if (execution.status) {
        execBits.push(`Status ${execution.status}`)
      }
      if (execBits.length > 0) {
        parts.push(execBits.join(" • "))
      }
    }

    if (maybeResult && typeof maybeResult === "object") {
      const result = maybeResult as { output?: unknown }
      if (typeof result.output === "string") {
        parts.push(result.output)
      } else if (result.output !== undefined) {
        try {
          parts.push(JSON.stringify(result.output))
        } catch {
          parts.push(String(result.output))
        }
      }
    }

    if (parts.length > 0) {
      return parts.join(" • ")
    }
  }
  return String(entry ?? "")
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
        <Leaf className="size-5 text-muted-foreground/60" />
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
                  onRefresh={onRetry}
                />
              ))}
            </div>
          </CollapsibleSection>
        )
      })}
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
  onRefresh,
}: {
  session: AgentSessionWithStatus
  onRefresh?: () => void
}) {
  const workspaceId = useWorkspaceId()
  const createdAt = new Date(session.created_at)
  const { toast } = useToast()
  const [expandedCompleted, setExpandedCompleted] = useState<Set<string>>(
    new Set()
  )
  const [expandedPending, setExpandedPending] = useState<Set<string>>(new Set())
  const [formState, setFormState] = useState<Record<string, DecisionFormState>>(
    {}
  )
  const [formError, setFormError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const toggleCompletedExpanded = (approvalId: string, open?: boolean) => {
    setExpandedCompleted((prev) => {
      const next = new Set(prev)
      const shouldOpen = open ?? !next.has(approvalId)
      if (shouldOpen) {
        next.add(approvalId)
      } else {
        next.delete(approvalId)
      }
      return next
    })
  }

  const togglePendingExpanded = (toolCallId: string, open?: boolean) => {
    setExpandedPending((prev) => {
      const next = new Set(prev)
      const shouldOpen = open ?? !next.has(toolCallId)
      if (shouldOpen) {
        next.add(toolCallId)
      } else {
        next.delete(toolCallId)
      }
      return next
    })
  }

  const handleCopySessionId = async () => {
    try {
      await navigator.clipboard.writeText(session.id)
      toast({
        title: "Session ID copied",
        description: "The session identifier is now in your clipboard.",
      })
    } catch {
      toast({
        title: "Copy failed",
        description: "Unable to copy the session ID. Please try again.",
      })
    }
  }

  const humanizeActionRef = (ref: string): string =>
    ref
      .replace(/[_-]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .replace(/\b\w/g, (char) => char.toUpperCase())

  const pendingApprovals = useMemo(
    () =>
      session.pendingApprovalCount > 0
        ? (session.approvals?.filter(
            (approval) => approval.status === "pending"
          ) ?? [])
        : [],
    [session.approvals, session.pendingApprovalCount]
  )

  useEffect(() => {
    setFormState((prev) => {
      const next: Record<string, DecisionFormState> = {}
      let changed = false
      for (const approval of pendingApprovals) {
        const existing = prev[approval.tool_call_id]
        if (existing) {
          next[approval.tool_call_id] = existing
        } else {
          next[approval.tool_call_id] = createDefaultDecisionState()
          changed = true
        }
      }
      if (Object.keys(prev).length !== Object.keys(next).length) {
        changed = true
      }
      return changed ? next : prev
    })
    const validIds = new Set(
      pendingApprovals.map((approval) => approval.tool_call_id)
    )
    setExpandedPending((prev) => {
      if (prev.size === 0) {
        return prev
      }
      const next = new Set(Array.from(prev).filter((id) => validIds.has(id)))
      return next.size === prev.size ? prev : next
    })
    if (pendingApprovals.length === 0) {
      setFormError(null)
    }
  }, [pendingApprovals])

  const updateFormState = useCallback(
    (
      toolCallId: string,
      updater: (state: DecisionFormState) => DecisionFormState
    ) => {
      setFormState((prev) => {
        const current = prev[toolCallId] ?? createDefaultDecisionState()
        const nextState = updater(current)
        if (
          current.decision === nextState.decision &&
          current.overrideArgs === nextState.overrideArgs &&
          current.message === nextState.message
        ) {
          return prev
        }
        return {
          ...prev,
          [toolCallId]: nextState,
        }
      })
    },
    []
  )

  const handleDecisionChange = useCallback(
    (toolCallId: string, decision: DecisionType) => {
      updateFormState(toolCallId, (current) => ({
        ...current,
        decision,
      }))
      setExpandedPending((prev) => {
        const next = new Set(prev)
        if (decision === "override" || decision === "deny") {
          next.add(toolCallId)
        } else {
          next.delete(toolCallId)
        }
        return next
      })
    },
    [updateFormState]
  )

  const handleOverrideChange = useCallback(
    (toolCallId: string, value: string) => {
      updateFormState(toolCallId, (current) => ({
        ...current,
        overrideArgs: value,
      }))
    },
    [updateFormState]
  )

  const handleMessageChange = useCallback(
    (toolCallId: string, value: string) => {
      updateFormState(toolCallId, (current) => ({
        ...current,
        message: value,
      }))
    },
    [updateFormState]
  )

  const handleSubmit = async () => {
    if (!workspaceId) {
      setFormError("Workspace context is required to submit approvals.")
      return
    }
    if (!pendingApprovals.length) {
      setFormError("There are no pending approvals to submit.")
      return
    }

    const approvalsPayload: Record<string, AgentApprovalDecisionPayload> = {}

    for (const approval of pendingApprovals) {
      const state = formState[approval.tool_call_id]
      if (!state) {
        setFormError("Please review all pending approvals before submitting.")
        return
      }

      let value: AgentApprovalDecisionPayload
      if (state.decision === "approve") {
        value = true
      } else if (state.decision === "override") {
        const trimmed = state.overrideArgs.trim()
        let overrideArgs: Record<string, unknown> | undefined
        if (trimmed.length > 0) {
          try {
            overrideArgs = JSON.parse(trimmed)
          } catch {
            setFormError(
              `Override args for tool ${approval.tool_call_id} must be valid JSON.`
            )
            return
          }
        }
        value = {
          kind: "tool-approved",
          override_args: overrideArgs,
        }
      } else {
        const message = state.message.trim()
        value =
          message.length > 0
            ? { kind: "tool-denied", message }
            : { kind: "tool-denied" }
      }

      const validation = approvalValueValidator.safeParse(value)
      if (!validation.success) {
        setFormError(
          `Approval payload for tool ${approval.tool_call_id} is invalid: ${validation.error.message}`
        )
        return
      }

      approvalsPayload[approval.tool_call_id] = validation.data
    }

    setIsSubmitting(true)
    setFormError(null)
    try {
      await agentSubmitAgentApprovals({
        workspaceId,
        sessionId: session.id,
        requestBody: {
          approvals: approvalsPayload,
        },
      })
      toast({
        title: "Approvals submitted",
        description: "The agent will resume once the workflow processes them.",
      })
      setFormState({})
      setExpandedPending(new Set())
      onRefresh?.()
    } catch (error) {
      console.error("Failed to submit approvals", error)
      setFormError("Failed to submit approvals. Please try again.")
    } finally {
      setIsSubmitting(false)
    }
  }

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

  const approvalsToDisplay = pendingApprovals

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
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                size="icon"
                className="h-7 w-7 text-muted-foreground hover:text-foreground"
              >
                <MoreHorizontal className="size-4" />
                <span className="sr-only">Session actions</span>
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-40">
              <DropdownMenuLabel>Session actions</DropdownMenuLabel>
              <DropdownMenuItem onSelect={handleCopySessionId}>
                Copy session ID
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          <span>{createdAt.toLocaleString()}</span>
          <span aria-hidden="true">•</span>
          <span>{shortTimeAgo(createdAt)}</span>
        </div>
        {session.pendingApprovalCount > 0 && (
          <span
            className={cn(
              "inline-flex w-fit items-center rounded-full px-2 py-0.5 text-xs font-medium",
              toneBadgeClasses[session.statusTone]
            )}
          >
            {session.pendingApprovalCount} pending approval
            {session.pendingApprovalCount > 1 ? "s" : ""}
          </span>
        )}
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
                    type="button"
                    key={approval.id}
                    onClick={() => {
                      if (hasExpandableContent) {
                        toggleCompletedExpanded(approval.id)
                      }
                    }}
                    className="flex w-full items-start gap-2 rounded-md border border-border/50 bg-muted/20 px-2 py-1.5 text-left transition-colors hover:bg-muted/30 disabled:opacity-50"
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
                    <div className="flex min-w-0 flex-1 flex-col gap-0.5">
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
                            open={expandedCompleted.has(approval.id)}
                            onOpenChange={(value) =>
                              toggleCompletedExpanded(approval.id, value)
                            }
                          >
                            <CollapsibleTrigger
                              onClick={(e) => e.stopPropagation()}
                              className="flex shrink-0 text-[11px] font-medium text-muted-foreground/80 transition-colors hover:text-muted-foreground"
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
                          open={expandedCompleted.has(approval.id)}
                          onOpenChange={(value) =>
                            toggleCompletedExpanded(approval.id, value)
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
          <div className="flex flex-col gap-2 rounded-lg border border-border/60 bg-card px-3 py-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-[11px] font-semibold uppercase tracking-wide text-amber-800">
                Pending approvals
              </span>
              <span className="text-xs font-medium text-muted-foreground">
                {pendingApprovals.length} total
              </span>
            </div>
            <div className="flex flex-col gap-2">
              {approvalsToDisplay.map((approval) => {
                const recommendationDisplay = getRecommendationDisplay(
                  approval.recommendation?.verdict
                )
                const reason = approval.recommendation?.reason?.trim() ?? ""
                const toolLabel = approval.tool_name
                  ? reconstructActionType(approval.tool_name)
                  : "Unknown tool"
                const isExpanded = expandedPending.has(approval.tool_call_id)
                const state =
                  formState[approval.tool_call_id] ??
                  createDefaultDecisionState()
                const showRecommendationCallout =
                  recommendationDisplay.verdict !== "unknown"
                const argsData = approval.tool_call_args
                let parsedArgs: unknown = argsData
                if (typeof argsData === "string") {
                  try {
                    parsedArgs = JSON.parse(argsData)
                  } catch {
                    parsedArgs = argsData
                  }
                }

                return (
                  <div
                    key={approval.id}
                    className="rounded-md border border-border/60 bg-background px-3 py-2 shadow-sm dark:bg-slate-950"
                  >
                    <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
                      <div className="flex items-center gap-2">
                        {getIcon(toolLabel, {
                          className: "size-4 text-muted-foreground/70",
                        })}
                        <span className="text-xs font-semibold text-foreground">
                          {toolLabel}
                        </span>
                      </div>
                      <div className="flex flex-wrap items-center gap-2 sm:justify-end">
                        <HoverCard openDelay={100} closeDelay={100}>
                          <HoverCardTrigger asChild>
                            <div
                              className={cn(
                                "cursor-help",
                                recommendationDisplay.badgeClassName
                              )}
                              role="button"
                              tabIndex={0}
                            >
                              <recommendationDisplay.icon
                                className={cn(
                                  "size-3",
                                  recommendationDisplay.iconClassName
                                )}
                              />
                              {recommendationDisplay.label}
                            </div>
                          </HoverCardTrigger>
                          <HoverCardContent className="w-72 space-y-2 text-[11px] leading-snug">
                            <p className="font-medium text-foreground">
                              {recommendationDisplay.label}
                            </p>
                            <p>{reason || recommendationDisplay.description}</p>
                            {approval.recommendation?.generated_by ? (
                              <p className="text-[10px] uppercase tracking-wide text-muted-foreground/70">
                                Source: {approval.recommendation.generated_by}
                              </p>
                            ) : null}
                          </HoverCardContent>
                        </HoverCard>
                        <div className="flex items-center">
                          <Label
                            htmlFor={`decision-${approval.tool_call_id}`}
                            className="sr-only"
                          >
                            Decision for {toolLabel}
                          </Label>
                          <Select
                            value={state.decision}
                            onValueChange={(value) =>
                              handleDecisionChange(
                                approval.tool_call_id,
                                value as DecisionType
                              )
                            }
                          >
                            <SelectTrigger
                              id={`decision-${approval.tool_call_id}`}
                              className="h-8 min-w-[160px] text-xs"
                            >
                              <SelectValue placeholder="Select an action" />
                            </SelectTrigger>
                            <SelectContent className="min-w-[180px]">
                              <SelectItem value="approve">Approve</SelectItem>
                              <SelectItem value="override">
                                Approve with overrides
                              </SelectItem>
                              <SelectItem value="deny">Deny</SelectItem>
                            </SelectContent>
                          </Select>
                        </div>
                        <Button
                          size="icon"
                          variant="ghost"
                          className="h-7 w-7 text-muted-foreground hover:text-foreground"
                          onClick={() =>
                            togglePendingExpanded(approval.tool_call_id)
                          }
                          aria-label={
                            isExpanded
                              ? "Collapse approval details"
                              : "Expand approval details"
                          }
                          aria-expanded={isExpanded}
                        >
                          <ChevronDownIcon
                            className={cn(
                              "size-3 transition-transform",
                              isExpanded ? "rotate-180" : "rotate-0"
                            )}
                          />
                        </Button>
                      </div>
                    </div>
                    {isExpanded ? (
                      <ScrollArea className="mt-3 max-h-72 pr-2">
                        <div className="space-y-3 text-xs text-muted-foreground">
                          {showRecommendationCallout ? (
                            <Alert
                              className={cn(
                                recommendationDisplay.surfaceClassName,
                                "border-l-4 pl-8"
                              )}
                            >
                              <AlertTitle
                                className={cn(
                                  "flex items-center gap-2 text-sm font-semibold",
                                  recommendationDisplay.accentTextClassName
                                )}
                              >
                                <recommendationDisplay.icon
                                  className={cn(
                                    "size-4",
                                    recommendationDisplay.iconClassName
                                  )}
                                />
                                AI recommends{" "}
                                {recommendationDisplay.label.toLowerCase()}
                              </AlertTitle>
                              <AlertDescription className="text-xs text-muted-foreground">
                                {reason || recommendationDisplay.description}
                              </AlertDescription>
                            </Alert>
                          ) : null}
                          <div className="space-y-1">
                            <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground/75">
                              Arguments
                            </span>
                            <JsonViewWithControls
                              src={parsedArgs}
                              defaultExpanded
                              defaultTab="nested"
                              showControls={false}
                              className="text-xs"
                            />
                          </div>
                          <div className="space-y-1">
                            <span className="text-[10px] font-semibold uppercase tracking-wide text-muted-foreground/75">
                              Recent context
                            </span>
                            {approval.history && approval.history.length > 0 ? (
                              <ul className="space-y-1 text-[11px] leading-snug text-muted-foreground">
                                {approval.history.map((entry, index) => (
                                  <li
                                    key={`${approval.id}-history-${index}`}
                                    className="flex gap-2"
                                  >
                                    <span className="text-muted-foreground/60">
                                      {index + 1}.
                                    </span>
                                    <span className="flex-1">
                                      {summarizeHistoryEntry(entry)}
                                    </span>
                                  </li>
                                ))}
                              </ul>
                            ) : (
                              <p className="text-[11px] italic text-muted-foreground/70">
                                No recent agent outputs available for this
                                approval.
                              </p>
                            )}
                          </div>
                          <div className="space-y-3">
                            {state.decision === "override" ? (
                              <div className="flex flex-col gap-1.5">
                                <Label className="text-xs text-muted-foreground">
                                  Override arguments (JSON)
                                </Label>
                                <Textarea
                                  value={state.overrideArgs}
                                  className="min-h-[96px] text-xs font-mono"
                                  placeholder='e.g. { "channel": "general" }'
                                  onChange={(event) =>
                                    handleOverrideChange(
                                      approval.tool_call_id,
                                      event.target.value
                                    )
                                  }
                                />
                              </div>
                            ) : null}
                            {state.decision === "deny" ? (
                              <div className="flex flex-col gap-1.5">
                                <Label className="text-xs text-muted-foreground">
                                  Optional reason
                                </Label>
                                <Textarea
                                  value={state.message}
                                  className="min-h-[72px] text-xs"
                                  placeholder="Let the agent know why this call is denied."
                                  onChange={(event) =>
                                    handleMessageChange(
                                      approval.tool_call_id,
                                      event.target.value
                                    )
                                  }
                                />
                              </div>
                            ) : null}
                          </div>
                        </div>
                      </ScrollArea>
                    ) : null}
                  </div>
                )
              })}
            </div>
            {formError ? (
              <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
                {formError}
              </div>
            ) : null}
            <div className="flex justify-end">
              <Button
                type="button"
                size="sm"
                className="h-8 px-3 text-xs"
                onClick={handleSubmit}
                disabled={isSubmitting || pendingApprovals.length === 0}
              >
                {isSubmitting ? "Submitting..." : "Submit decisions"}
              </Button>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
