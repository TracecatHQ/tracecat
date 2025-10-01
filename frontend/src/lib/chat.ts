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
