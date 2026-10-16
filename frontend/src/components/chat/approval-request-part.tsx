"use client"

import {
  ChevronRightIcon,
  CopyIcon,
  Loader2,
  PencilIcon,
  ShieldAlertIcon,
} from "lucide-react"
import {
  type ReactNode,
  useCallback,
  useEffect,
  useMemo,
  useState,
} from "react"
import type { ApprovalDecision } from "@/client"
import {
  Tool,
  ToolContent,
  ToolHeader,
  ToolInput,
  type ToolState,
} from "@/components/ai-elements/tool"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { getIcon } from "@/components/icons"
import { JsonViewWithControls } from "@/components/json-viewer"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { Input } from "@/components/ui/input"
import { toast } from "@/components/ui/use-toast"
import {
  type ApprovalCard,
  isToolApprovedDecision,
  isToolDeniedDecision,
} from "@/lib/chat"
import { cn, copyToClipboard, reconstructActionType } from "@/lib/utils"

const TOOL_ICON_PROPS = { className: "size-5 p-[3px]" } as const

type DecisionState = {
  action: ApprovalDecision["action"] | undefined
  reason?: string
  overrideArgs?: string
}

function submittedDecisionFromApproval(
  approval: ApprovalCard
): DecisionState | undefined {
  if (approval.status === "approved") {
    if (
      isToolApprovedDecision(approval.decision) &&
      approval.decision.override_args !== undefined
    ) {
      return {
        action: "override",
        overrideArgs: formatArgs(approval.decision.override_args),
      }
    }
    return { action: "approve" }
  }

  if (approval.status === "rejected") {
    const reason =
      approval.reason ??
      (isToolDeniedDecision(approval.decision) &&
      typeof approval.decision.message === "string"
        ? approval.decision.message
        : undefined)
    return { action: "deny", reason }
  }

  return undefined
}

function approvalStateKey(approval: ApprovalCard): string {
  return JSON.stringify({
    id: approval.tool_call_id,
    status: approval.status ?? "pending",
    decision: approval.decision ?? null,
    reason: approval.reason ?? null,
  })
}

/** The approvals being submitted were already decided on another surface. */
export class ApprovalsAlreadyDecidedError extends Error {}

export function ApprovalRequestPart({
  approvals,
  onSubmit,
  canSubmit = true,
}: {
  approvals: ApprovalCard[]
  /** Resolves with the tool call IDs whose decisions it sent. */
  onSubmit?: (decisions: ApprovalDecision[]) => Promise<string[]>
  /** False until the backend has saved the pause the decisions answer. */
  canSubmit?: boolean
}) {
  const [decisions, setDecisions] = useState<Record<string, DecisionState>>({})
  const [submittedDecisions, setSubmittedDecisions] = useState<
    Record<string, DecisionState>
  >({})
  const [submitting, setSubmitting] = useState(false)
  const [sendingIds, setSendingIds] = useState<ReadonlySet<string>>(new Set())
  const [decidedElsewhere, setDecidedElsewhere] = useState(false)

  const approvalsKey = useMemo(
    () => approvals.map(approvalStateKey).join(":"),
    [approvals]
  )

  useEffect(() => {
    const nextSubmittedDecisions: Record<string, DecisionState> = {}
    for (const approval of approvals) {
      const submittedDecision = submittedDecisionFromApproval(approval)
      if (submittedDecision) {
        nextSubmittedDecisions[approval.tool_call_id] = submittedDecision
      }
    }
    setDecisions({})
    setSubmittedDecisions(nextSubmittedDecisions)
    setDecidedElsewhere(false)
  }, [approvalsKey])

  const hasSelectedPendingDecision =
    approvals.length > 0 &&
    approvals.some((approval) => {
      if (submittedDecisions[approval.tool_call_id]) {
        return false
      }
      return Boolean(decisions[approval.tool_call_id]?.action)
    })

  const setDecision = useCallback(
    (toolCallId: string, update: Partial<DecisionState>) => {
      setDecisions((prev) => ({
        ...prev,
        [toolCallId]: {
          ...prev[toolCallId],
          ...update,
        },
      }))
    },
    []
  )

  /** Sends the given pending decisions; already submitted calls are skipped. */
  const send = useCallback(
    async (selected: Record<string, DecisionState>) => {
      if (!onSubmit) {
        return
      }
      const payload: ApprovalDecision[] = []
      const acceptedDecisions: Record<string, DecisionState> = {}
      for (const approval of approvals) {
        if (submittedDecisions[approval.tool_call_id]) {
          continue
        }
        const decision = selected[approval.tool_call_id]
        if (!decision?.action) {
          continue
        }
        if (decision.action === "approve") {
          payload.push({
            tool_call_id: approval.tool_call_id,
            action: "approve",
          })
        } else if (decision.action === "override") {
          const overrideArgs = parseOverrideArgs(decision.overrideArgs)
          if (!overrideArgs) {
            toast({
              variant: "destructive",
              title: "Invalid override args",
              description: `Override args for ${approval.tool_name} must be a JSON object.`,
            })
            return
          }
          payload.push({
            tool_call_id: approval.tool_call_id,
            action: "override",
            override_args: overrideArgs,
          })
        } else {
          payload.push({
            tool_call_id: approval.tool_call_id,
            action: "deny",
            reason: decision.reason ?? "",
          })
        }
        acceptedDecisions[approval.tool_call_id] = decision
      }

      try {
        setSubmitting(true)
        setSendingIds(new Set(Object.keys(acceptedDecisions)))
        const sentIds = new Set(await onSubmit(payload))
        setSubmittedDecisions((prev) => {
          const next = { ...prev }
          for (const [toolCallId, decision] of Object.entries(
            acceptedDecisions
          )) {
            // A decision made elsewhere meanwhile was not sent, so keep its
            // recorded outcome rather than show this tab's choice.
            next[toolCallId] = sentIds.has(toolCallId)
              ? decision
              : (prev[toolCallId] ?? { action: undefined })
          }
          return next
        })
        setDecisions((prev) => {
          const next = { ...prev }
          for (const toolCallId of Object.keys(acceptedDecisions)) {
            delete next[toolCallId]
          }
          return next
        })
      } catch (error) {
        if (error instanceof ApprovalsAlreadyDecidedError) {
          setDecidedElsewhere(true)
        } else {
          console.error(error)
        }
      } finally {
        setSubmitting(false)
        setSendingIds(new Set())
      }
    },
    [approvals, onSubmit, submittedDecisions]
  )

  const handleSubmit = useCallback(async () => {
    if (!onSubmit || !hasSelectedPendingDecision) {
      toast({
        title: "Pending decisions",
        description:
          "Choose an action for at least one tool before continuing.",
      })
      return
    }
    await send(decisions)
  }, [decisions, hasSelectedPendingDecision, onSubmit, send])

  const disabled = !onSubmit || decidedElsewhere || submitting

  if (!approvals.length) {
    return null
  }

  // A lone call sends on click; a batch is chosen call by call, then submitted.
  const pending = approvals.filter(
    (approval) =>
      !submittedDecisions[approval.tool_call_id] &&
      !sendingIds.has(approval.tool_call_id)
  )
  const pendingDecided = pending.filter(
    (approval) => decisions[approval.tool_call_id]?.action
  ).length

  function toggleEditing(approval: ApprovalCard) {
    const isEditing = decisions[approval.tool_call_id]?.action === "override"
    setDecision(
      approval.tool_call_id,
      isEditing
        ? { action: undefined, overrideArgs: undefined }
        : { action: "override", overrideArgs: formatArgs(approval.args) }
    )
  }

  function renderArgs(approval: ApprovalCard) {
    const decision = decisions[approval.tool_call_id]
    if (decision?.action !== "override") {
      return <ApprovalArgs args={approval.args} />
    }
    return (
      <div data-testid={`approval-override-editor-${approval.tool_call_id}`}>
        <CodeEditor
          value={decision.overrideArgs ?? formatArgs(approval.args)}
          language="json"
          readOnly={disabled}
          onChange={(value) =>
            setDecision(approval.tool_call_id, {
              action: "override",
              overrideArgs: value,
            })
          }
          className={cn(
            "text-xs",
            "[&_.cm-editor]:!border [&_.cm-editor]:!border-input [&_.cm-editor]:!bg-background [&_.cm-editor]:rounded-md",
            "[&_.cm-scroller]:h-auto [&_.cm-scroller]:min-h-24 [&_.cm-scroller]:max-h-80 [&_.cm-scroller]:overflow-auto"
          )}
        />
      </div>
    )
  }

  function renderEditButton(approval: ApprovalCard) {
    const isEditing = decisions[approval.tool_call_id]?.action === "override"
    return (
      <Button
        type="button"
        size="sm"
        variant="ghost"
        aria-pressed={isEditing}
        disabled={disabled}
        onClick={() => toggleEditing(approval)}
        className="h-7 gap-1.5 px-2 text-xs text-muted-foreground"
      >
        <PencilIcon className="size-3" />
        {isEditing ? "Discard edits" : "Edit arguments"}
      </Button>
    )
  }

  return (
    <div className="space-y-1">
      {approvals
        .filter((approval) => !pending.includes(approval))
        .map((approval) => {
          const toolCallId = approval.tool_call_id
          const actionId = reconstructActionType(approval.tool_name)
          return (
            <div key={toolCallId} data-testid={`approval-card-${toolCallId}`}>
              <Tool>
                <ToolHeader
                  title={actionId}
                  state={getApprovalToolState(
                    submittedDecisions[toolCallId],
                    sendingIds.has(toolCallId)
                  )}
                  icon={getIcon(actionId, TOOL_ICON_PROPS)}
                />
                <ToolContent>
                  <ToolInput input={approval.args} />
                </ToolContent>
              </Tool>
            </div>
          )
        })}
      {pending.length === 1 &&
        pending.map((approval) => {
          const toolCallId = approval.tool_call_id
          const decision = decisions[toolCallId]
          const isEditing = decision?.action === "override"
          return (
            <section
              key={toolCallId}
              data-testid={`approval-card-${toolCallId}`}
              aria-label="Approval required"
              className="my-2 overflow-hidden rounded-lg border bg-background"
            >
              <ApprovalHeader>Waiting for your approval</ApprovalHeader>
              <div className="space-y-2.5 p-3">
                <ApprovalToolName toolName={approval.tool_name} />
                {renderArgs(approval)}
              </div>
              <footer className="flex items-center justify-between gap-2 border-t px-3 py-2">
                <div>
                  {hasArgs(approval.args) && renderEditButton(approval)}
                </div>
                <div className="flex items-center gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={disabled || !canSubmit}
                    onClick={() =>
                      void send({ [toolCallId]: { action: "deny" } })
                    }
                    className="h-7 px-3 text-xs"
                  >
                    Deny
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    disabled={disabled || !canSubmit}
                    onClick={() =>
                      void send({
                        [toolCallId]: isEditing
                          ? decision
                          : { action: "approve" },
                      })
                    }
                    className="h-7 bg-foreground px-3 text-xs text-background hover:bg-foreground/85"
                  >
                    {isEditing ? "Approve with edits" : "Approve"}
                  </Button>
                </div>
              </footer>
            </section>
          )
        })}
      {pending.length > 1 && (
        <section
          data-testid="approval-batch"
          aria-label="Approvals required"
          className="my-2 overflow-hidden rounded-lg border bg-background"
        >
          <ApprovalHeader>
            {pending.length} tool calls waiting for your approval
          </ApprovalHeader>
          <ul className="divide-y">
            {pending.map((approval) => {
              const toolCallId = approval.tool_call_id
              const actionId = reconstructActionType(approval.tool_name)
              const decision = decisions[toolCallId]
              const isEditing = decision?.action === "override"
              const approved = decision?.action === "approve" || isEditing
              const denied = decision?.action === "deny"
              return (
                <li
                  key={toolCallId}
                  data-testid={`approval-card-${toolCallId}`}
                >
                  <Collapsible>
                    <div className="flex items-center gap-2 px-3 py-2">
                      <CollapsibleTrigger className="group flex min-w-0 flex-1 items-center gap-2 text-left">
                        <ChevronRightIcon className="size-3.5 shrink-0 text-muted-foreground transition-transform group-data-[state=open]:rotate-90" />
                        <span className="shrink-0 font-mono text-xs">
                          {actionId}
                        </span>
                        <span className="min-w-0 truncate font-mono text-xs text-muted-foreground">
                          {summarizeArgs(approval.args)}
                        </span>
                      </CollapsibleTrigger>
                      <div
                        role="group"
                        aria-label={`Decision for ${actionId}`}
                        className="flex shrink-0 overflow-hidden rounded-md border"
                      >
                        <button
                          type="button"
                          aria-pressed={denied}
                          disabled={disabled}
                          onClick={() =>
                            setDecision(toolCallId, { action: "deny" })
                          }
                          className="h-6 px-2.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50 aria-pressed:bg-muted aria-pressed:font-medium aria-pressed:text-foreground"
                        >
                          Deny
                        </button>
                        <button
                          type="button"
                          aria-pressed={approved}
                          disabled={disabled}
                          onClick={() =>
                            setDecision(
                              toolCallId,
                              isEditing ? decision : { action: "approve" }
                            )
                          }
                          className="h-6 border-l px-2.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-50 aria-pressed:bg-foreground aria-pressed:font-medium aria-pressed:text-background"
                        >
                          {isEditing ? "Approve with edits" : "Approve"}
                        </button>
                      </div>
                    </div>
                    <CollapsibleContent>
                      <div className="space-y-2 px-3 pb-3 pl-8">
                        {renderArgs(approval)}
                        {hasArgs(approval.args) && renderEditButton(approval)}
                      </div>
                    </CollapsibleContent>
                  </Collapsible>
                  {denied && (
                    <div className="px-3 pb-3 pl-8">
                      <Input
                        aria-label={`Deny reason for ${actionId}`}
                        value={decision.reason ?? ""}
                        onChange={(event) =>
                          setDecision(toolCallId, {
                            action: "deny",
                            reason: event.target.value,
                          })
                        }
                        placeholder="Reason, sent to the agent"
                        disabled={disabled}
                        className="h-7 text-xs"
                      />
                    </div>
                  )}
                </li>
              )
            })}
          </ul>
          <footer className="flex items-center justify-between gap-2 border-t px-3 py-2">
            <span className="text-xs text-muted-foreground">
              {pendingDecided} of {pending.length} decided
            </span>
            <div className="flex items-center gap-2">
              <Button
                type="button"
                size="sm"
                variant="outline"
                disabled={disabled || pendingDecided === pending.length}
                onClick={() => {
                  for (const approval of pending) {
                    if (!decisions[approval.tool_call_id]?.action) {
                      setDecision(approval.tool_call_id, { action: "approve" })
                    }
                  }
                }}
                className="h-7 px-3 text-xs"
              >
                Approve rest
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={handleSubmit}
                disabled={disabled || !canSubmit || !hasSelectedPendingDecision}
                className="h-7 bg-foreground px-3 text-xs text-background hover:bg-foreground/85"
              >
                {submitting && <Loader2 className="size-3 animate-spin" />}
                Submit decisions
              </Button>
            </div>
          </footer>
        </section>
      )}
      {!onSubmit && (
        <p className="text-xs text-muted-foreground">
          Approval submission is not available in this context.
        </p>
      )}
      {decidedElsewhere && (
        <p className="text-xs text-muted-foreground">
          This approval was already decided elsewhere.
        </p>
      )}
    </div>
  )
}

function ApprovalHeader({ children }: { children: ReactNode }) {
  return (
    <div className="flex items-center gap-2 border-b bg-muted/40 px-3 py-2 text-xs font-medium">
      <ShieldAlertIcon className="size-3.5 shrink-0 text-amber-500" />
      {children}
    </div>
  )
}

function ApprovalToolName({ toolName }: { toolName: string }) {
  const actionId = reconstructActionType(toolName)
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      {getIcon(actionId, { className: "size-6 shrink-0 p-1" })}
      <p className="min-w-0 truncate text-sm font-medium">
        Run <span className="font-mono text-[13px]">{actionId}</span>
      </p>
    </div>
  )
}

// Keys stay quiet; strings and constants take the code block's GitHub hues.
const ARGS_JSON_CLASS = cn(
  "max-h-80 rounded-none border-0 border-t bg-transparent [&_[role=tabpanel]]:px-3 [&_[role=tabpanel]]:py-2",
  "[&_.json-view]:text-muted-foreground [&_.json-view]:[--json-property:hsl(var(--muted-foreground))] [&_.json-view]:[--json-index:hsl(var(--muted-foreground))]",
  "[&_.json-view]:[--json-string:#032f62] [&_.json-view]:[--json-number:#6f42c1] [&_.json-view]:[--json-boolean:#6f42c1] [&_.json-view]:[--json-null:#6f42c1]",
  "dark:[&_.json-view]:[--json-string:#9ecbff] dark:[&_.json-view]:[--json-number:#b392f0] dark:[&_.json-view]:[--json-boolean:#b392f0] dark:[&_.json-view]:[--json-null:#b392f0]"
)

/** Big nested values start folded so the top-level keys stay in view. */
function foldLargeNested({ depth, size }: { depth: number; size: number }) {
  return depth > 1 && size > 5
}

/** A pending call's arguments, folded behind an "Arguments" toggle. */
function ApprovalArgs({ args }: { args: unknown }) {
  const value = parseArgs(args)
  const count = countArgs(value)
  if (count === 0) {
    return (
      <p className="rounded-md border bg-muted/30 px-3 py-2 text-xs text-muted-foreground">
        No arguments
      </p>
    )
  }
  return (
    <Collapsible defaultOpen className="rounded-md border bg-muted/30">
      <div className="flex h-7 items-center justify-between gap-2 pl-1.5 pr-1">
        <CollapsibleTrigger className="group flex items-center gap-1 rounded px-1 text-xs text-muted-foreground hover:text-foreground">
          <ChevronRightIcon className="size-3 transition-transform group-data-[state=open]:rotate-90" />
          Arguments
          <span className="text-muted-foreground/70">{count}</span>
        </CollapsibleTrigger>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          aria-label="Copy arguments"
          onClick={() =>
            copyToClipboard({
              value: formatArgs(value),
              message: "Copied arguments",
            })
          }
          className="size-5 text-muted-foreground [&_svg]:size-3"
        >
          <CopyIcon />
        </Button>
      </div>
      <CollapsibleContent>
        <JsonViewWithControls
          src={value}
          showControls={false}
          defaultExpanded
          collapsed={foldLargeNested}
          className={ARGS_JSON_CLASS}
        />
      </CollapsibleContent>
    </Collapsible>
  )
}

/** Arguments as data, parsing a JSON string when the call sent one. */
function parseArgs(args: unknown): unknown {
  if (typeof args !== "string") {
    return args ?? {}
  }
  try {
    return JSON.parse(args)
  } catch {
    return args
  }
}

function countArgs(value: unknown): number {
  if (value === null || value === undefined) {
    return 0
  }
  if (Array.isArray(value)) {
    return value.length
  }
  if (typeof value === "object") {
    return Object.keys(value).length
  }
  return 1
}

function hasArgs(args: unknown): boolean {
  return countArgs(parseArgs(args)) > 0
}

/** One line of top-level arguments for a batch row, e.g. "limit: 5, ids: [2]". */
function summarizeArgs(args: unknown): string {
  const value = parseArgs(args)
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return countArgs(value) === 0 ? "No arguments" : formatArgs(value)
  }
  const parts = Object.entries(value).map(([key, item]) => {
    if (Array.isArray(item)) {
      return `${key}: [${item.length}]`
    }
    if (item && typeof item === "object") {
      return `${key}: {…}`
    }
    return `${key}: ${typeof item === "string" ? item : JSON.stringify(item)}`
  })
  return parts.length ? parts.join(", ") : "No arguments"
}

/** The tool line state for a call, from its decision and whether it is sending. */
function getApprovalToolState(
  submitted: DecisionState | undefined,
  isSending: boolean
): ToolState {
  if (submitted?.action === "deny") {
    return "output-denied"
  }
  if (submitted || isSending) {
    return "input-available"
  }
  return "approval-requested"
}

/** Override args as a JSON object, or null when the text is not one. */
function parseOverrideArgs(
  text: string | undefined
): Record<string, unknown> | null {
  if (!text) {
    return {}
  }
  try {
    const parsed: unknown = JSON.parse(text)
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as Record<string, unknown>
    }
    return null
  } catch {
    return null
  }
}

function formatArgs(args: unknown): string {
  if (args == null) return "{}"
  if (typeof args === "string") {
    try {
      return JSON.stringify(JSON.parse(args), null, 2)
    } catch {
      return args
    }
  }
  try {
    return JSON.stringify(args, null, 2)
  } catch {
    return String(args)
  }
}
