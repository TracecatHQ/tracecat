"use client"

import type { JSONSchema7 } from "json-schema"
import { AlertTriangleIcon } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import { approvalsSubmitApprovals } from "@/client"
import { getIcon } from "@/components/icons"
import { JsonViewWithControls } from "@/components/json-viewer"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
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
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useToast } from "@/components/ui/use-toast"
import type {
  AgentApprovalDecisionPayload,
  AgentSessionWithStatus,
} from "@/lib/agents"
import { jsonSchemaToZod } from "@/lib/jsonschema"
import { reconstructActionType } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

const APPROVAL_VALUE_SCHEMA: JSONSchema7 = {
  oneOf: [
    {
      type: "boolean",
    },
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

type AgentApprovalsDialogProps = {
  session: AgentSessionWithStatus | null
  open: boolean
  onOpenChange: (open: boolean) => void
  onSubmitted?: () => void
}

type DecisionType = "approve" | "override" | "deny"

type DecisionFormState = {
  decision: DecisionType
  overrideArgs: string
  message: string
}

export function AgentApprovalsDialog({
  session,
  open,
  onOpenChange,
  onSubmitted,
}: AgentApprovalsDialogProps) {
  const workspaceId = useWorkspaceId()
  const { toast } = useToast()
  const formatWorkflowLabel = (
    summary?: AgentSessionWithStatus["parent_workflow"]
  ): string => {
    if (!summary) return "Unknown workflow"
    return summary.alias ? `${summary.title} (${summary.alias})` : summary.title
  }
  const [formState, setFormState] = useState<Record<string, DecisionFormState>>(
    {}
  )
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [formError, setFormError] = useState<string | null>(null)

  const pendingApprovals = useMemo(
    () =>
      session?.approvals?.filter((approval) => approval.status === "pending") ??
      [],
    [session]
  )

  useEffect(() => {
    if (!open || !pendingApprovals.length) return
    const defaults: Record<string, DecisionFormState> = {}
    for (const approval of pendingApprovals) {
      defaults[approval.tool_call_id] = {
        decision: "approve",
        overrideArgs: "",
        message: "",
      }
    }
    setFormState(defaults)
    setFormError(null)
  }, [open, pendingApprovals])

  const handleDecisionChange = (toolCallId: string, decision: DecisionType) => {
    setFormState((prev) => ({
      ...prev,
      [toolCallId]: {
        decision,
        overrideArgs: prev[toolCallId]?.overrideArgs ?? "",
        message: prev[toolCallId]?.message ?? "",
      },
    }))
  }

  const handleSubmit = async () => {
    if (!session || !workspaceId) return
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
        let overrideArgs: Record<string, unknown> | null = null
        const trimmed = state.overrideArgs.trim()
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
          override_args: overrideArgs ?? undefined,
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
      await approvalsSubmitApprovals({
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
      onSubmitted?.()
      onOpenChange(false)
    } catch (error) {
      console.error("Failed to submit approvals", error)
      setFormError("Failed to submit approvals. Please try again.")
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Dialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (!nextOpen) {
          setFormError(null)
        }
        onOpenChange(nextOpen)
      }}
    >
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Pending approvals</DialogTitle>
          <DialogDescription>
            Review the tool calls issued by this agent and submit your decision.
          </DialogDescription>
        </DialogHeader>

        {!session ? (
          <div className="flex items-center gap-2 rounded-md border border-dashed border-muted-foreground/40 p-4 text-sm text-muted-foreground">
            <AlertTriangleIcon className="size-4" />
            <span>No agent session selected.</span>
          </div>
        ) : pendingApprovals.length === 0 ? (
          <div className="flex items-center gap-2 rounded-md border border-dashed border-muted-foreground/40 p-4 text-sm text-muted-foreground">
            <AlertTriangleIcon className="size-4" />
            <span>This session has no pending approvals.</span>
          </div>
        ) : (
          <ScrollArea className="max-h-[420px] pr-2">
            <div className="space-y-4">
              {pendingApprovals.map((approval) => {
                const state = formState[approval.tool_call_id] ?? {
                  decision: "approve" as DecisionType,
                  overrideArgs: "",
                  message: "",
                }
                const actionTypeKey = approval.tool_name
                  ? reconstructActionType(approval.tool_name)
                  : "unknown"
                const defaultActionLabel = approval.tool_name
                  ? actionTypeKey
                  : "Unknown action"
                const actionDisplayName =
                  session?.action_title ?? defaultActionLabel
                let rootWorkflowLabel = session?.root_workflow ?? null
                if (
                  rootWorkflowLabel &&
                  session?.parent_workflow &&
                  rootWorkflowLabel.id === session.parent_workflow.id
                ) {
                  rootWorkflowLabel = null
                }

                // Parse args if it's a JSON string
                const argsData = approval.tool_call_args
                let parsedArgs = argsData
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
                    className="rounded-xl border border-border/70 bg-card p-4 shadow-sm"
                  >
                    <div className="mb-3 flex flex-wrap items-center gap-2">
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <div className="flex items-center gap-2">
                            {getIcon(actionTypeKey, {
                              className: "size-4 shrink-0",
                              flairsize: "sm",
                            })}
                            <span className="text-sm font-semibold">
                              {actionDisplayName}
                            </span>
                          </div>
                        </TooltipTrigger>
                        <TooltipContent>
                          <span className="font-mono text-[11px]">
                            {approval.tool_call_id}
                          </span>
                        </TooltipContent>
                      </Tooltip>
                    </div>

                    <div className="mb-4 space-y-1 text-xs text-muted-foreground">
                      <div>
                        <span className="font-medium text-foreground">
                          Parent:
                        </span>{" "}
                        {formatWorkflowLabel(session?.parent_workflow)}
                      </div>
                      {rootWorkflowLabel ? (
                        <div>
                          <span className="font-medium text-foreground">
                            Root:
                          </span>{" "}
                          {formatWorkflowLabel(rootWorkflowLabel)}
                        </div>
                      ) : null}
                      <div>
                        <span className="font-medium text-foreground">
                          Action:
                        </span>{" "}
                        {actionDisplayName}
                        {session?.action_ref ? (
                          <span className="ml-1 text-muted-foreground/80">
                            ({session.action_ref})
                          </span>
                        ) : null}
                      </div>
                    </div>

                    <div className="mb-4 text-xs text-muted-foreground">
                      <div className="mb-1 font-medium uppercase tracking-wide">
                        Arguments
                      </div>
                      <JsonViewWithControls
                        src={parsedArgs}
                        defaultExpanded
                        defaultTab="nested"
                        showControls={false}
                        className="text-xs"
                      />
                    </div>

                    <div className="space-y-3">
                      <div className="flex flex-col gap-1.5">
                        <Label className="text-xs text-muted-foreground">
                          Decision
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
                          <SelectTrigger className="h-8 w-fit min-w-[180px] text-xs">
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

                      {state.decision === "override" && (
                        <div className="flex flex-col gap-1.5">
                          <Label className="text-xs text-muted-foreground">
                            Override arguments (JSON)
                          </Label>
                          <Textarea
                            value={state.overrideArgs}
                            className="min-h-[96px] text-xs font-mono"
                            placeholder='e.g. { "channel": "general" }'
                            onChange={(event) =>
                              setFormState((prev) => ({
                                ...prev,
                                [approval.tool_call_id]: {
                                  ...state,
                                  overrideArgs: event.target.value,
                                },
                              }))
                            }
                          />
                        </div>
                      )}

                      {state.decision === "deny" && (
                        <div className="flex flex-col gap-1.5">
                          <Label className="text-xs text-muted-foreground">
                            Optional reason
                          </Label>
                          <Textarea
                            value={state.message}
                            className="min-h-[72px] text-xs"
                            placeholder="Let the agent know why this call is denied."
                            onChange={(event) =>
                              setFormState((prev) => ({
                                ...prev,
                                [approval.tool_call_id]: {
                                  ...state,
                                  message: event.target.value,
                                },
                              }))
                            }
                          />
                        </div>
                      )}
                    </div>
                  </div>
                )
              })}
            </div>
          </ScrollArea>
        )}

        {formError && (
          <div className="rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
            {formError}
          </div>
        )}

        <DialogFooter className="flex w-full justify-between">
          <Button
            type="button"
            variant="ghost"
            onClick={() => onOpenChange(false)}
            disabled={isSubmitting}
          >
            Cancel
          </Button>
          <Button
            type="button"
            onClick={handleSubmit}
            disabled={
              isSubmitting ||
              !session ||
              !pendingApprovals.length ||
              !workspaceId
            }
          >
            {isSubmitting ? "Submitting..." : "Submit decisions"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
