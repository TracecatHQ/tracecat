"use client"

import {
  Activity,
  Bell,
  BriefcaseBusiness,
  FileText,
  GitBranch,
  KeyRound,
  type LucideIcon,
  Plug,
  Table2,
} from "lucide-react"
import type { MissionControlArtifact } from "@/types/mission-control"

export type ArtifactConfig = {
  label: string
  singularLabel: string
  icon: LucideIcon
  href?: (artifact: MissionControlArtifact, workspaceId: string) => string
}

export const ARTIFACT_REGISTRY = {
  alert: {
    label: "Alerts",
    singularLabel: "Alert",
    icon: Bell,
  },
  case: {
    label: "Cases",
    singularLabel: "Case",
    icon: BriefcaseBusiness,
    href: (artifact, workspaceId) =>
      `/workspaces/${workspaceId}/cases/${artifact.id}`,
  },
  workflow: {
    label: "Workflows",
    singularLabel: "Workflow",
    icon: GitBranch,
    href: (artifact, workspaceId) =>
      `/workspaces/${workspaceId}/workflows/${artifact.id}`,
  },
  run: {
    label: "Runs",
    singularLabel: "Run",
    icon: Activity,
    href: (artifact, workspaceId) =>
      artifact.type === "run"
        ? `/workspaces/${workspaceId}/workflows/${artifact.workflowId}/executions/${artifact.id}`
        : "#",
  },
  table: {
    label: "Tables",
    singularLabel: "Table",
    icon: Table2,
    href: (artifact, workspaceId) =>
      `/workspaces/${workspaceId}/tables/${artifact.id}`,
  },
  integration: {
    label: "Integrations",
    singularLabel: "Integration",
    icon: Plug,
  },
  secret: {
    label: "Secrets",
    singularLabel: "Secret",
    icon: KeyRound,
  },
  generic: {
    label: "Results",
    singularLabel: "Result",
    icon: FileText,
  },
} satisfies Record<MissionControlArtifact["type"], ArtifactConfig>

const UNKNOWN_ARTIFACT_CONFIG: ArtifactConfig = {
  label: "Results",
  singularLabel: "Result",
  icon: FileText,
}

/** Return display metadata for a Mission Control artifact. */
export function getArtifactConfig(
  artifact: MissionControlArtifact
): ArtifactConfig {
  return (
    ARTIFACT_REGISTRY[artifact.type as keyof typeof ARTIFACT_REGISTRY] ??
    UNKNOWN_ARTIFACT_CONFIG
  )
}

/** Return the full-page href for an artifact when one exists. */
export function getArtifactHref(
  artifact: MissionControlArtifact,
  workspaceId: string
): string | undefined {
  return getArtifactConfig(artifact).href?.(artifact, workspaceId)
}
