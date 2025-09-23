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
