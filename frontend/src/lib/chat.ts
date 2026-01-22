import type { QueryClient } from "@tanstack/react-query"
import * as ai from "ai"
import type { AgentSessionEntity, UIMessage } from "@/client"
import type { ApprovalCard } from "@/hooks/use-chat"

export function isAgentSessionEntity(
  value: unknown
): value is AgentSessionEntity {
  return (
    value === "case" ||
    value === "agent_preset" ||
    value === "agent_preset_builder" ||
    value === "copilot" ||
    value === "workflow"
  )
}

/**
 * Type guard to check if a part is a data part.
 * DataUIPart has a `data` property and type starting with "data-"
 */
function isDataPart(
  part: UIMessage["parts"][number]
): part is Extract<UIMessage["parts"][number], { data: unknown }> {
  return "data" in part
}

/**
 * Type guard to check if a part is a tool part (has state property).
 */
function isToolPart(
  part: UIMessage["parts"][number]
): part is Extract<UIMessage["parts"][number], { state: string }> {
  return (
    "state" in part &&
    typeof (part as { state?: string }).state === "string" &&
    ((part as { state: string }).state === "input-streaming" ||
      (part as { state: string }).state === "input-available" ||
      (part as { state: string }).state === "output-available" ||
      (part as { state: string }).state === "output-error")
  )
}

/**
 * Type guard for basic UI parts (text, reasoning, file, etc.).
 */
function isBasicUIPart(
  part: UIMessage["parts"][number]
): part is Extract<
  UIMessage["parts"][number],
  | { type: "text" }
  | { type: "reasoning" }
  | { type: "file" }
  | { type: "step-start" }
  | { type: "source-url" }
  | { type: "source-document" }
> {
  return (
    part.type === "text" ||
    part.type === "reasoning" ||
    part.type === "file" ||
    part.type === "step-start" ||
    part.type === "source-url" ||
    part.type === "source-document"
  )
}

export function toUIMessage(message: UIMessage): ai.UIMessage {
  return {
    id: message.id,
    role: message.role,
    parts: message.parts.map(
      (part): ai.UIMessagePart<ai.UIDataTypes, ai.UITools> => {
        // Type-narrowing with guards ensures proper typing
        if (isDataPart(part)) {
          return part as ai.DataUIPart<ai.UIDataTypes>
        }

        if (isToolPart(part)) {
          return part as ai.ToolUIPart
        }

        if (isBasicUIPart(part)) {
          // Cast needed due to nominal type differences (e.g., providerMetadata)
          // but types are structurally compatible
          return part as ai.UIMessagePart<ai.UIDataTypes, ai.UITools>
        }

        // Exhaustiveness check: TypeScript will error if new part types are added
        const _exhaustive: never = part
        throw new Error(
          `Unhandled UI message part type: ${JSON.stringify(part)}`
        )
      }
    ),
  }
}

const UPDATE_ON_ACTIONS: Partial<Record<AgentSessionEntity, Array<string>>> = {
  case: ["core.cases.update_case", "core.cases.create_comment"],
  agent_preset_builder: ["internal.builder.update_preset"],
}

// mapping from chatentity to
/**
 * Maps chat entity types to their query invalidation logic.
 * Each entity type defines how to invalidate related queries when updates occur.
 */
export const ENTITY_TO_INVALIDATION: Record<
  AgentSessionEntity,
  {
    predicate: (toolName: string) => boolean
    handler: (
      queryClient: QueryClient,
      workspaceId: string,
      entityId: string
    ) => void
  }
> = {
  case: {
    predicate: (toolName: string) =>
      Boolean(UPDATE_ON_ACTIONS.case?.includes(toolName)),
    handler: (queryClient, workspaceId, entityId) => {
      // Invalidate specific case query
      queryClient.invalidateQueries({ queryKey: ["case", entityId] })
      // Invalidate cases list for workspace
      queryClient.invalidateQueries({ queryKey: ["cases", workspaceId] })
      // Invalidate case events
      queryClient.invalidateQueries({
        queryKey: ["case-events", entityId, workspaceId],
      })
      // Invalidate case comments
      queryClient.invalidateQueries({
        queryKey: ["case-comments", entityId, workspaceId],
      })
    },
  },
  agent_preset: {
    predicate: () => false,
    handler: (_queryClient, _workspaceId, _entityId) => {
      // No invalidation logic for agent presets yet; placeholder for future support.
    },
  },
  agent_preset_builder: {
    predicate: (toolName: string) =>
      Boolean(UPDATE_ON_ACTIONS.agent_preset_builder?.includes(toolName)),
    handler: (queryClient, workspaceId, entityId) => {
      // Invalidate agent preset detail and workspace list
      queryClient.invalidateQueries({
        queryKey: ["agent-presets", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-preset", workspaceId, entityId],
      })
    },
  },
  copilot: {
    predicate: () => false,
    handler: (_queryClient, _workspaceId, _entityId) => {
      // No invalidation logic for copilot
    },
  },
  workflow: {
    predicate: () => false,
    handler: (_queryClient, _workspaceId, _entityId) => {
      // No invalidation logic for workflow entity
    },
  },
  approval: {
    predicate: () => false,
    handler: (_queryClient, _workspaceId, _entityId) => {
      // No invalidation logic for approval entity
    },
  },
}

export type ModelInfo = {
  name: string
  provider: string
  baseUrl?: string | null
}

/**
 * Concatenates all text parts from a message into a single string.
 * Skips non-text parts and preserves paragraph breaks between multiple parts.
 */
export function getAssistantText(parts: UIMessage["parts"]): string {
  if (!parts || parts.length === 0) {
    return ""
  }

  return parts.reduce<string>((accumulator, part) => {
    // Ignore non-text parts to avoid copying tool input/output.
    if (part.type !== "text") {
      return accumulator
    }

    const partText =
      "text" in part && typeof part.text === "string" ? part.text : ""

    if (partText.length === 0) {
      return accumulator
    }

    return accumulator.length > 0 ? `${accumulator}\n\n${partText}` : partText
  }, "")
}
/**
 * Transforms UI messages by managing tool call state visibility.
 *
 * Implements a state machine that tracks tool call lifecycle:
 * - input-available: Tool call initiated
 * - approval-request: Awaiting user approval
 * - output-available/output-error: Tool call completed
 *
 * Visibility rules:
 * - Streaming tool inputs remain visible
 * - Completed tool calls collapse their input and approval parts
 * - Pending approvals (without output) remain visible
 *
 * @param messages - Array of UI messages to transform
 * @returns Transformed messages with appropriate parts hidden/visible
 */
export function transformMessages(messages: ai.UIMessage[]): ai.UIMessage[] {
  // Tool call id to array positions (using string keys for positions)
  const states = new Map<
    string,
    { open?: string; approval?: string; close?: string } | undefined
  >()
  // Array positions to ignore (using "msgIndex-partIndex" string format)
  const ignorePos = new Set<string>()

  for (const [i, message] of messages.entries()) {
    for (const [j, part] of message.parts.entries()) {
      const posKey = `${i}-${j}`

      if (ai.isToolUIPart(part)) {
        const { state, toolCallId } = part
        if (state === "input-available") {
          // OPEN STATE
          // If we encounter an input-available part, we open a tool call state
          states.set(toolCallId, { open: posKey })
        } else if (state === "output-available" || state === "output-error") {
          // CLOSE STATE
          // If we encounter an output-* part:
          // 1. Close the tool call state by hiding the input-* + approval parts
          // 2. Merge the input args into the output part
          const currState = states.get(toolCallId)
          if (currState) {
            if (currState.open) {
              ignorePos.add(currState.open) // Hide open state
            }
            if (currState.approval) {
              ignorePos.add(currState.approval) // Hide approval state
            }
          } else {
            console.warn(`Tool call ${toolCallId} not found in states`)
          }
          // add close state
          states.set(toolCallId, { ...currState, close: posKey })
        }
      } else if (part.type === "data-approval-request") {
        // Handle approval request parts
        // 1. If approval request we mark positions, only ignore if we hit a close state
        // 2. If we see approval requests after a close state, we should ignore the approval requests
        const approvals: ApprovalCard[] = Array.isArray(part.data)
          ? part.data
          : []
        for (const approval of approvals) {
          // For each approval find the matching open state and update the approval state
          if (approval.tool_call_id) {
            const currState = states.get(approval.tool_call_id)
            if (currState) {
              // If we already have a close state, ignore this approval request
              if (currState.close) {
                ignorePos.add(posKey)
              }
              states.set(approval.tool_call_id, {
                ...currState,
                approval: posKey,
              })
            }
          }
        }
      }
    }
  }

  // Finally walk through each message and filter out the ignored positions
  const finalMessages: ai.UIMessage[] = []
  for (const [i, message] of messages.entries()) {
    const newParts: ai.UIMessagePart<ai.UIDataTypes, ai.UITools>[] = []
    for (const [j, part] of message.parts.entries()) {
      const posKey = `${i}-${j}`
      if (ignorePos.has(posKey)) continue
      // Merge input from open state into output parts
      if (
        ai.isToolUIPart(part) &&
        (part.state === "output-available" || part.state === "output-error")
      ) {
        // Handle output parts
        const { toolCallId } = part
        const currState = states.get(toolCallId)
        const newPart: ai.ToolUIPart = {
          ...part,
        }
        if (currState?.open) {
          // Extract the open position from the string key
          const [openMsgIdx, openPartIdx] = currState.open
            .split("-")
            .map(Number)
          const openPart = messages[openMsgIdx].parts[openPartIdx]
          if (!ai.isToolUIPart(openPart)) {
            throw new Error(
              `Open part is not a tool part: ${JSON.stringify(openPart)}`
            )
          }
          newPart.input = openPart.input
        }
        newParts.push(newPart)
      } else {
        newParts.push(part)
      }
    }
    if (newParts.length > 0) {
      finalMessages.push({ ...message, parts: newParts })
    }
  }
  console.log({
    states,
    ignorePos,
    finalMessages,
  })
  return finalMessages
}
