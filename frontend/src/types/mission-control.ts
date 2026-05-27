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

export type MissionControlArtifact =
  | CaseArtifact
  | WorkflowArtifact
  | RunArtifact
  | TableArtifact
  | AlertArtifact
  | IntegrationArtifact
  | SecretArtifact
  | GenericArtifact

export type ArtifactType = MissionControlArtifact["type"]

export type ArtifactOp = "upsert" | "remove"

export type ArtifactDataPayload = {
  op: ArtifactOp
  artifact: MissionControlArtifact
}

export type ArtifactLane = {
  agentType: string | undefined
  agentId: string | undefined
  artifacts: MissionControlArtifact[]
}

export const ARTIFACT_DATA_PART_TYPE = "data-artifact"

type UnknownRecord = Record<string, unknown>

/** Build the stable tab key for a Mission Control artifact. */
export function artifactKey(
  artifact: Pick<MissionControlArtifact, "type" | "id">
): string {
  return `${artifact.type}:${artifact.id}`
}

function isRecord(value: unknown): value is UnknownRecord {
  return typeof value === "object" && value !== null
}

function isArtifact(value: unknown): value is MissionControlArtifact {
  if (!isRecord(value)) {
    return false
  }
  return (
    typeof value.type === "string" &&
    typeof value.id === "string" &&
    typeof value.title === "string"
  )
}

/** Parse a Vercel UI message part as a Mission Control artifact payload. */
export function getArtifactDataPayload(
  part: UIMessage["parts"][number]
): ArtifactDataPayload | undefined {
  if (part.type !== ARTIFACT_DATA_PART_TYPE || !("data" in part)) {
    return undefined
  }
  const data = part.data
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
