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
import type { WorkspaceChatArtifact } from "@/types/workspace-chat-artifacts"

export type ArtifactConfig = {
  label: string
  singularLabel: string
  icon: LucideIcon
  href?: (artifact: WorkspaceChatArtifact, workspaceId: string) => string
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
} satisfies Record<WorkspaceChatArtifact["type"], ArtifactConfig>

const UNKNOWN_ARTIFACT_CONFIG: ArtifactConfig = {
  label: "Results",
  singularLabel: "Result",
  icon: FileText,
}

/** Return display metadata for a Workspace chat artifact. */
export function getArtifactConfig(
  artifact: WorkspaceChatArtifact
): ArtifactConfig {
  return (
    ARTIFACT_REGISTRY[artifact.type as keyof typeof ARTIFACT_REGISTRY] ??
    UNKNOWN_ARTIFACT_CONFIG
  )
}

/** Return the full-page href for an artifact when one exists. */
export function getArtifactHref(
  artifact: WorkspaceChatArtifact,
  workspaceId: string
): string | undefined {
  return getArtifactConfig(artifact).href?.(artifact, workspaceId)
}
