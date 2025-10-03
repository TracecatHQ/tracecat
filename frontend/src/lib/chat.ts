import type { QueryClient } from "@tanstack/react-query"
import type * as ai from "ai"
import type {
  BuiltinToolCallEvent,
  BuiltinToolResultEvent,
  ChatEntity,
  FinalResultEvent,
  FunctionToolCallEvent,
  FunctionToolResultEvent,
  ModelRequest,
  ModelResponse,
  PartDeltaEvent,
  PartStartEvent,
  UIMessage,
} from "@/client"

export type ModelMessage = ModelRequest | ModelResponse

export function isModelMessage(value: unknown): value is ModelMessage {
  return (
    typeof value === "object" &&
    value !== null &&
    "kind" in value &&
    (value.kind === "request" || value.kind === "response") &&
    "parts" in value &&
    Array.isArray(value.parts)
  )
}

export function isChatEntity(value: unknown): value is ChatEntity {
  return value === "case"
}

const streamEventKinds = [
  "part_delta",
  "part_start",
  "final_result",
  "function_tool_call",
  "function_tool_result",
  "builtin_tool_call",
  "builtin_tool_result",
] as const

export function isStreamEvent(
  data: unknown
): data is
  | PartStartEvent
  | PartDeltaEvent
  | FinalResultEvent
  | FunctionToolCallEvent
  | FunctionToolResultEvent
  | BuiltinToolCallEvent
  | BuiltinToolResultEvent {
  return (
    typeof data === "object" &&
    data !== null &&
    "event_kind" in data &&
    streamEventKinds.includes(
      data.event_kind as (typeof streamEventKinds)[number]
    )
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

const CASE_UPDATE_ACTIONS = new Set([
  "core__cases__update_case",
  "core__cases__create_comment",
])

const RUNBOOK_UPDATE_ACTIONS = new Set(["core__runbooks__update_runbook"])

// mapping from chatentity to
/**
 * Maps chat entity types to their query invalidation logic.
 * Each entity type defines how to invalidate related queries when updates occur.
 */
export const ENTITY_TO_INVALIDATION: Record<
  ChatEntity,
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
    predicate: (toolName: string) => CASE_UPDATE_ACTIONS.has(toolName),
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
  runbook: {
    predicate: (toolName: string) => RUNBOOK_UPDATE_ACTIONS.has(toolName),
    handler: (queryClient, workspaceId, entityId) => {
      // Invalidate all runbook queries (non-exact match)
      queryClient.invalidateQueries({ queryKey: ["runbooks"], exact: false })
    },
  },
}

export type ModelInfo = {
  name: string
  provider: string
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
