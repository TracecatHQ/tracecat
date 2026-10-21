"use client"

import type { JSONSchema7 } from "json-schema"
import { AlertTriangleIcon } from "lucide-react"
import { useMemo } from "react"
import { type ApprovalDecision, approvalsSubmitApprovals } from "@/client"
import { ApprovalRequestPart } from "@/components/chat/approval-request-part"
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { useToast } from "@/components/ui/use-toast"
import { invalidateChatTurnQueries } from "@/hooks/use-chat"
import type {
  AgentApprovalDecisionPayload,
  AgentSessionWithStatus,
} from "@/lib/agents"
import type { ApprovalCard } from "@/lib/chat"
import { jsonSchemaToZod } from "@/lib/jsonschema"
import { useQueryClient } from "@/lib/query"
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

function toPayload(decision: ApprovalDecision): AgentApprovalDecisionPayload {
  if (decision.action === "approve") {
    return true
  }
  if (decision.action === "override") {
    return {
      kind: "tool-approved",
      override_args: decision.override_args ?? undefined,
    }
  }
  const message = decision.reason?.trim()
  return message ? { kind: "tool-denied", message } : { kind: "tool-denied" }
}

function parseArgs(args: unknown): unknown {
  if (typeof args !== "string") return args
  try {
    return JSON.parse(args)
  } catch {
    return args
  }
}

export function AgentApprovalsDialog({
  session,
  open,
  onOpenChange,
  onSubmitted,
}: AgentApprovalsDialogProps) {
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()
  const { toast } = useToast()

  const pendingApprovals = useMemo<ApprovalCard[]>(
    () =>
      (session?.approvals ?? [])
        .filter((approval) => approval.status === "pending")
        .map((approval) => ({
          tool_call_id: approval.tool_call_id,
          tool_name: approval.tool_name ?? "unknown",
          args: parseArgs(approval.tool_call_args),
        })),
    [session]
  )

  const workflowLabel = session?.parent_workflow
    ? session.parent_workflow.alias
      ? `${session.parent_workflow.title} (${session.parent_workflow.alias})`
      : session.parent_workflow.title
    : null

  const handleSubmit = async (
    decisions: ApprovalDecision[]
  ): Promise<string[]> => {
    if (!session || !workspaceId) return []
    const approvals: Record<string, AgentApprovalDecisionPayload> = {}
    for (const decision of decisions) {
      const validation = approvalValueValidator.safeParse(toPayload(decision))
      if (!validation.success) {
        throw new Error(
          `Approval payload for tool ${decision.tool_call_id} is invalid: ${validation.error.message}`
        )
      }
      approvals[decision.tool_call_id] = validation.data
    }
    try {
      await approvalsSubmitApprovals({
        workspaceId,
        sessionId: session.id,
        requestBody: { approvals },
      })
    } catch (error) {
      console.error("Failed to submit approvals", error)
      toast({
        variant: "destructive",
        title: "Approval submission failed",
        description: "Please try again.",
      })
      throw error
    }
    invalidateChatTurnQueries(queryClient, {
      chatId: session.id,
      workspaceId,
    })
    toast({
      title: "Approvals submitted",
      description: "The agent will resume once the workflow processes them.",
    })
    onSubmitted?.()
    if (decisions.length === pendingApprovals.length) {
      onOpenChange(false)
    }
    return decisions.map(({ tool_call_id }) => tool_call_id)
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="lg" className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Pending approvals</DialogTitle>
          <DialogDescription>
            {workflowLabel
              ? `Tool calls from ${session?.action_title ?? "this agent"} in ${workflowLabel}.`
              : "Review the tool calls issued by this agent."}
          </DialogDescription>
        </DialogHeader>
        <DialogBody>
          {pendingApprovals.length === 0 ? (
            <div className="flex items-center gap-2 rounded-md border border-dashed border-muted-foreground/40 p-4 text-sm text-muted-foreground">
              <AlertTriangleIcon className="size-4" />
              <span>
                {session
                  ? "This session has no pending approvals."
                  : "No agent session selected."}
              </span>
            </div>
          ) : (
            <ApprovalRequestPart
              key={pendingApprovals.map((a) => a.tool_call_id).join(":")}
              approvals={pendingApprovals}
              onSubmit={handleSubmit}
            />
          )}
        </DialogBody>
      </DialogContent>
    </Dialog>
  )
}
