"use client"

import { Loader2 } from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"
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
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { toast } from "@/components/ui/use-toast"
import {
  type ApprovalCard,
  isToolApprovedDecision,
  isToolDeniedDecision,
} from "@/lib/chat"
import { cn, reconstructActionType } from "@/lib/utils"

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
  const pendingCount = approvals.filter(
    (approval) => !submittedDecisions[approval.tool_call_id]
  ).length
  // A lone call sends on click; a batch is chosen call by call, then submitted.
  const sendsOnClick = pendingCount === 1

  if (!approvals.length) {
    return null
  }

  return (
    <div className="space-y-1">
      {approvals.map((approval) => {
        const toolCallId = approval.tool_call_id
        const actionId = reconstructActionType(approval.tool_name)
        const decision = decisions[toolCallId]
        const submittedDecision = submittedDecisions[toolCallId]
        const isEditing = decision?.action === "override"
        const isSending = sendingIds.has(toolCallId)
        const isPending = !submittedDecision && !isSending
        function choose(update: DecisionState) {
          if (sendsOnClick) {
            void send({ [toolCallId]: update })
          } else {
            setDecision(toolCallId, update)
          }
        }
        return (
          <div key={toolCallId} data-testid={`approval-card-${toolCallId}`}>
            <Tool defaultOpen>
              <ToolHeader
                title={actionId}
                state={getApprovalToolState(submittedDecision, isSending)}
                icon={getIcon(actionId, TOOL_ICON_PROPS)}
              />
              <ToolContent>
                {isEditing ? (
                  <div data-testid={`approval-override-editor-${toolCallId}`}>
                    <CodeEditor
                      value={decision.overrideArgs ?? formatArgs(approval.args)}
                      language="json"
                      readOnly={disabled}
                      onChange={(value) =>
                        setDecision(toolCallId, {
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
                ) : (
                  <ToolInput input={approval.args} />
                )}
                {isPending && decision?.action === "deny" && (
                  <Textarea
                    className="text-xs"
                    rows={3}
                    value={decision.reason ?? ""}
                    onChange={(event) =>
                      setDecision(toolCallId, {
                        action: "deny",
                        reason: event.target.value,
                      })
                    }
                    placeholder="Share a short reason"
                    disabled={disabled}
                  />
                )}
                {isPending && (
                  <div className="flex flex-wrap gap-1.5 [&>button]:h-7 [&>button]:px-2.5 [&>button]:text-xs">
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      aria-pressed={
                        sendsOnClick
                          ? undefined
                          : decision?.action === "approve"
                      }
                      disabled={disabled || (sendsOnClick && !canSubmit)}
                      onClick={() =>
                        choose(isEditing ? decision : { action: "approve" })
                      }
                      className="aria-pressed:bg-muted"
                    >
                      {isEditing ? "Approve with edits" : "Approve"}
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="outline"
                      aria-pressed={
                        sendsOnClick ? undefined : decision?.action === "deny"
                      }
                      disabled={disabled || (sendsOnClick && !canSubmit)}
                      onClick={() => choose({ action: "deny" })}
                      className="aria-pressed:bg-muted"
                    >
                      Deny
                    </Button>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-pressed={isEditing}
                      disabled={disabled}
                      onClick={() =>
                        setDecision(
                          toolCallId,
                          isEditing
                            ? { action: undefined, overrideArgs: undefined }
                            : {
                                action: "override",
                                overrideArgs: formatArgs(approval.args),
                              }
                        )
                      }
                      className="aria-pressed:bg-muted"
                    >
                      Edit
                    </Button>
                  </div>
                )}
              </ToolContent>
            </Tool>
          </div>
        )
      })}
      {pendingCount > 1 && (
        <div className="flex">
          <Button
            type="button"
            size="sm"
            className="h-7 px-2.5 text-xs"
            onClick={handleSubmit}
            disabled={disabled || !canSubmit || !hasSelectedPendingDecision}
          >
            {submitting && <Loader2 className="size-3 animate-spin" />}
            Submit
          </Button>
        </div>
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
