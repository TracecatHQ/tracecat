"use client"

import { CheckIcon, ClockIcon, Loader2, PencilIcon, XIcon } from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import type { ApprovalDecision } from "@/client"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { getIcon } from "@/components/icons"
import { JsonViewWithControls } from "@/components/json-viewer"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
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

  const allApprovalsSubmitted =
    approvals.length > 0 &&
    approvals.every((approval) =>
      Boolean(submittedDecisions[approval.tool_call_id])
    )

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

  const handleSubmit = useCallback(async () => {
    if (!onSubmit || !hasSelectedPendingDecision) {
      toast({
        title: "Pending decisions",
        description:
          "Choose an action for at least one tool before continuing.",
      })
      return
    }

    const payload: ApprovalDecision[] = []
    const acceptedDecisions: Record<string, DecisionState> = {}
    for (const approval of approvals) {
      if (submittedDecisions[approval.tool_call_id]) {
        continue
      }
      const decision = decisions[approval.tool_call_id]
      if (!decision?.action) {
        continue
      }
      if (decision.action === "approve") {
        payload.push({ tool_call_id: approval.tool_call_id, action: "approve" })
        acceptedDecisions[approval.tool_call_id] = decision
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
        acceptedDecisions[approval.tool_call_id] = decision
      } else if (decision.action === "deny") {
        payload.push({
          tool_call_id: approval.tool_call_id,
          action: "deny",
          reason: decision.reason ?? "",
        })
        acceptedDecisions[approval.tool_call_id] = decision
      }
    }

    try {
      setSubmitting(true)
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
    }
  }, [
    approvals,
    decisions,
    hasSelectedPendingDecision,
    onSubmit,
    submittedDecisions,
  ])

  const disabled = !onSubmit || decidedElsewhere

  if (!approvals.length) {
    return null
  }

  return (
    <div className="space-y-4">
      <div className="space-y-3">
        {approvals.map((approval, index) => {
          const actionId = reconstructActionType(approval.tool_name)
          const decision = decisions[approval.tool_call_id]
          const submittedDecision = submittedDecisions[approval.tool_call_id]
          const visibleDecision = submittedDecision ?? decision
          const isSubmitted = Boolean(submittedDecision)
          const initialOverrideArgs = formatArgs(approval.args)
          const isLastApproval = index === approvals.length - 1
          return (
            <div
              key={approval.tool_call_id}
              data-testid={`approval-card-${approval.tool_call_id}`}
              className={cn(
                "space-y-3 rounded-md border border-border/60 bg-background p-3 transition-colors",
                isSubmitted && "bg-muted/10",
                !isSubmitted &&
                  visibleDecision?.action !== undefined &&
                  "border-foreground/20"
              )}
            >
              <div className="flex flex-wrap items-center justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <div className="flex min-w-0 flex-wrap items-center gap-2.5">
                    {getIcon(actionId, TOOL_ICON_PROPS)}
                    <p className="min-w-0 truncate font-medium text-sm">
                      {actionId}
                    </p>
                    {isSubmitted ? null : <ApprovalRequiredBadge />}
                  </div>
                </div>
                <JsonViewWithControls
                  src={approval.args}
                  className="max-h-64"
                  showControls={false}
                  defaultExpanded
                />
                <div className="flex w-full flex-wrap justify-start gap-1 sm:w-auto [&>button]:h-6 [&>button]:rounded-lg">
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={disabled || submitting || isSubmitted}
                    onClick={() =>
                      setDecision(approval.tool_call_id, {
                        action: "approve",
                        reason: undefined,
                        overrideArgs: undefined,
                      })
                    }
                    className={cn(
                      visibleDecision?.action === "approve" &&
                        "border-success/55 text-success hover:border-success/65 hover:bg-background hover:text-success disabled:opacity-100"
                    )}
                  >
                    <CheckIcon className="mr-1 size-3" />
                    Approve
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={disabled || submitting || isSubmitted}
                    onClick={() =>
                      setDecision(approval.tool_call_id, {
                        action: "override",
                        reason: undefined,
                        overrideArgs:
                          decision?.overrideArgs ?? initialOverrideArgs,
                      })
                    }
                    className={cn(
                      visibleDecision?.action === "override" &&
                        "border-success/55 text-success hover:border-success/65 hover:bg-background hover:text-success disabled:opacity-100"
                    )}
                  >
                    <PencilIcon className="mr-1 size-3" />
                    Edit + approve
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="outline"
                    disabled={disabled || submitting || isSubmitted}
                    onClick={() =>
                      setDecision(approval.tool_call_id, {
                        action: "deny",
                        overrideArgs: undefined,
                      })
                    }
                    className={cn(
                      visibleDecision?.action === "deny" &&
                        "border-destructive/55 text-destructive hover:border-destructive/65 hover:bg-background hover:text-destructive disabled:opacity-100"
                    )}
                  >
                    <XIcon className="mr-1 size-3" />
                    Deny
                  </Button>
                </div>
              </div>
              {visibleDecision?.action === "override" && (
                <div
                  data-testid={`approval-override-editor-${approval.tool_call_id}`}
                >
                  <CodeEditor
                    value={visibleDecision.overrideArgs ?? initialOverrideArgs}
                    language="json"
                    readOnly={disabled || submitting || isSubmitted}
                    onChange={(value) => {
                      if (isSubmitted) {
                        return
                      }
                      setDecision(approval.tool_call_id, {
                        ...(decision ?? {}),
                        action: "override",
                        overrideArgs: value,
                      })
                    }}
                    className={cn(
                      "text-xs",
                      "[&_.cm-editor]:!border [&_.cm-editor]:!border-input [&_.cm-editor]:!bg-background [&_.cm-editor]:rounded-md",
                      "[&_.cm-scroller]:h-auto [&_.cm-scroller]:min-h-24 [&_.cm-scroller]:max-h-80 [&_.cm-scroller]:overflow-auto"
                    )}
                  />
                </div>
              )}
              {visibleDecision?.action === "deny" && (
                <Textarea
                  className="text-xs"
                  rows={3}
                  value={visibleDecision.reason ?? ""}
                  onChange={(event) =>
                    setDecision(approval.tool_call_id, {
                      ...(decision ?? {}),
                      action: "deny",
                      reason: event.target.value,
                    })
                  }
                  placeholder="Share a short reason"
                  disabled={disabled || submitting || isSubmitted}
                />
              )}
              {isLastApproval && (
                <div className="flex flex-wrap justify-end gap-2 pt-1">
                  <Button
                    type="button"
                    variant={allApprovalsSubmitted ? "outline" : "default"}
                    onClick={handleSubmit}
                    disabled={
                      disabled ||
                      !canSubmit ||
                      submitting ||
                      allApprovalsSubmitted ||
                      !hasSelectedPendingDecision
                    }
                    className={cn(
                      "h-6 gap-1 px-2 text-xs",
                      allApprovalsSubmitted &&
                        "border-border bg-muted text-muted-foreground disabled:opacity-100"
                    )}
                  >
                    {submitting ? (
                      <Loader2 className="size-3 animate-spin" />
                    ) : (
                      <CheckIcon className="size-3" />
                    )}
                    {submitting && "Submitting..."}
                    {!submitting && allApprovalsSubmitted && "Submitted"}
                    {!submitting && !allApprovalsSubmitted && "Submit"}
                  </Button>
                </div>
              )}
            </div>
          )
        })}
      </div>
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

function ApprovalRequiredBadge() {
  return (
    <TooltipProvider delayDuration={0}>
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            aria-label="Approval required"
            className="inline-flex items-center"
            role="img"
          >
            <ClockIcon className="size-3.5 text-amber-500 animate-pulse" />
            <span className="sr-only">Approval required</span>
          </span>
        </TooltipTrigger>
        <TooltipContent side="top">Approval required</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
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
