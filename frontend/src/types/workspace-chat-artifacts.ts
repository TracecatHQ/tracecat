import type { UIMessage } from "ai"

export type ArtifactScope = {
  agentId?: string
  agentType?: string
  parentToolCallId?: string
}

type BaseArtifact = {
  id: string
  title: string
  scope?: ArtifactScope
}

export type CaseArtifact = BaseArtifact & {
  type: "case"
  severity:
    | "unknown"
    | "informational"
    | "low"
    | "medium"
    | "high"
    | "critical"
    | "fatal"
    | "other"
  status:
    | "unknown"
    | "new"
    | "in_progress"
    | "on_hold"
    | "resolved"
    | "closed"
    | "other"
}

export type WorkflowArtifact = BaseArtifact & {
  type: "workflow"
  color: string
  isPublished?: boolean
}

export type RunArtifact = BaseArtifact & {
  type: "run"
  workflowId: string
  status: "running" | "success" | "failed" | "cancelled"
  startedAt: string
}

export type TableArtifact = BaseArtifact & {
  type: "table"
  rowCount?: number
}

export type AgentArtifact = BaseArtifact & {
  type: "agent"
}

export type AlertArtifact = BaseArtifact & {
  type: "alert"
}

export type IntegrationArtifact = BaseArtifact & {
  type: "integration"
}

export type SecretArtifact = BaseArtifact & {
  type: "secret"
}

export type GenericArtifact = BaseArtifact & {
  type: "generic"
  data?: Record<string, unknown>
}

export type WorkspaceChatArtifact =
  | CaseArtifact
  | WorkflowArtifact
  | RunArtifact
  | TableArtifact
  | AgentArtifact
  | AlertArtifact
  | IntegrationArtifact
  | SecretArtifact
  | GenericArtifact

export type ArtifactType = WorkspaceChatArtifact["type"]

export type ArtifactOp = "upsert" | "remove"

export type ArtifactDataPayload = {
  op: ArtifactOp
  artifact: WorkspaceChatArtifact
}

export const ARTIFACT_DATA_PART_TYPE = "data-artifact"

export type ArtifactDataPart = {
  type: typeof ARTIFACT_DATA_PART_TYPE
  data: ArtifactDataPayload
}

export type WorkspaceChatArtifactStreamPart = ArtifactDataPart

export type ArtifactLane = {
  agentType: string | undefined
  agentId: string | undefined
  artifacts: WorkspaceChatArtifact[]
}

type UnknownRecord = Record<string, unknown>
type StreamPartInput =
  | UIMessage["parts"][number]
  | { type: string; data?: unknown }

/** Build the stable tab key for a Workspace chat artifact. */
export function artifactKey(
  artifact: Pick<WorkspaceChatArtifact, "type" | "id">
): string {
  return `${artifact.type}:${artifact.id}`
}

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null
}

function isStringInSet<T extends string>(
  value: unknown,
  allowedValues: readonly T[]
): value is T {
  return typeof value === "string" && allowedValues.includes(value as T)
}

function isArtifactScope(value: unknown): value is ArtifactScope {
  if (value === undefined) {
    return true
  }
  if (!isRecord(value)) {
    return false
  }
  return (
    (value.agentId === undefined || typeof value.agentId === "string") &&
    (value.agentType === undefined || typeof value.agentType === "string") &&
    (value.parentToolCallId === undefined ||
      typeof value.parentToolCallId === "string")
  )
}

function isArtifact(value: unknown): value is WorkspaceChatArtifact {
  if (!isRecord(value)) {
    return false
  }
  if (
    typeof value.id !== "string" ||
    typeof value.title !== "string" ||
    !isArtifactScope(value.scope)
  ) {
    return false
  }

  switch (value.type) {
    case "case":
      return (
        isStringInSet(value.severity, [
          "unknown",
          "informational",
          "low",
          "medium",
          "high",
          "critical",
          "fatal",
          "other",
        ]) &&
        isStringInSet(value.status, [
          "unknown",
          "new",
          "in_progress",
          "on_hold",
          "resolved",
          "closed",
          "other",
        ])
      )
    case "workflow":
      return (
        typeof value.color === "string" &&
        (value.isPublished === undefined ||
          typeof value.isPublished === "boolean")
      )
    case "run":
      return (
        typeof value.workflowId === "string" &&
        isStringInSet(value.status, [
          "running",
          "success",
          "failed",
          "cancelled",
        ]) &&
        typeof value.startedAt === "string"
      )
    case "table":
      return value.rowCount === undefined || typeof value.rowCount === "number"
    case "agent":
      return true
    case "alert":
    case "integration":
    case "secret":
      return true
    case "generic":
      return value.data === undefined || isRecord(value.data)
    default:
      return false
  }
}

function parseArtifactDataPayload(
  data: unknown
): ArtifactDataPayload | undefined {
  if (!isRecord(data)) {
    return undefined
  }
  if (data.op !== "upsert" && data.op !== "remove") {
    return undefined
  }
  if (!isArtifact(data.artifact)) {
    return undefined
  }
  return data as ArtifactDataPayload
}

/** Parse a Vercel UI message part as a typed workspace chat stream part. */
export function parseWorkspaceChatArtifactStreamPart(
  part: StreamPartInput
): WorkspaceChatArtifactStreamPart | undefined {
  switch (part.type) {
    case ARTIFACT_DATA_PART_TYPE: {
      if (!("data" in part)) {
        return undefined
      }
      const data = parseArtifactDataPayload(part.data)
      if (!data) {
        return undefined
      }
      return {
        type: ARTIFACT_DATA_PART_TYPE,
        data,
      }
    }
    default:
      return undefined
  }
}
