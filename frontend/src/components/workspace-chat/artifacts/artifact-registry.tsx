"use client"

import type { QueryClient } from "@tanstack/react-query"
import {
  Bell,
  BlocksIcon,
  FileText,
  KeyRound,
  LayersIcon,
  ListVideoIcon,
  MousePointerClickIcon,
  Table2Icon,
  WorkflowIcon,
} from "lucide-react"
import type { ComponentType } from "react"
import { invalidateCaseActivityQueries } from "@/lib/cases/invalidation"
import type { WorkspaceChatArtifact } from "@/types/workspace-chat-artifacts"

export type ArtifactIconComponent = ComponentType<{ className?: string }>

type ArtifactQueryInvalidator = (
  queryClient: QueryClient,
  workspaceId: string,
  artifact: WorkspaceChatArtifact
) => void

export type ArtifactConfig = {
  label: string
  singularLabel: string
  icon: ArtifactIconComponent
  href?: (artifact: WorkspaceChatArtifact, workspaceId: string) => string
  invalidateQueries?: ArtifactQueryInvalidator
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
    icon: LayersIcon,
    href: (artifact, workspaceId) =>
      `/workspaces/${workspaceId}/cases/${artifact.id}`,
    invalidateQueries: (queryClient, workspaceId, artifact) => {
      invalidateCaseActivityQueries(queryClient, artifact.id, workspaceId)
      queryClient.invalidateQueries({
        queryKey: ["cases", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["cases", "paginated"],
        exact: false,
      })
    },
  },
  workflow: {
    label: "Workflows",
    singularLabel: "Workflow",
    icon: WorkflowIcon,
    href: (artifact, workspaceId) =>
      `/workspaces/${workspaceId}/workflows/${artifact.id}`,
    invalidateQueries: (queryClient, _workspaceId, artifact) => {
      // Workflow lists carry a 5-min stale time; refresh them so a chat-created
      // workflow shows up in dashboards and workflow/subflow pickers immediately.
      queryClient.invalidateQueries({ queryKey: ["workflows"] })
      queryClient.invalidateQueries({ queryKey: ["workflow", artifact.id] })
    },
  },
  run: {
    label: "Runs",
    singularLabel: "Run",
    icon: ListVideoIcon,
    href: (artifact, workspaceId) =>
      artifact.type === "run"
        ? `/workspaces/${workspaceId}/workflows/${artifact.workflowId}/executions/${artifact.id}`
        : "#",
  },
  table: {
    label: "Tables",
    singularLabel: "Table",
    icon: Table2Icon,
    href: (artifact, workspaceId) =>
      `/workspaces/${workspaceId}/tables/${artifact.id}`,
    invalidateQueries: (queryClient, workspaceId, artifact) => {
      queryClient.invalidateQueries({
        queryKey: ["table", workspaceId, artifact.id],
      })
      queryClient.invalidateQueries({
        queryKey: ["tables", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["rows", artifact.id],
      })
      queryClient.invalidateQueries({
        queryKey: ["rows", "paginated", artifact.id, workspaceId],
      })
    },
  },
  agent: {
    label: "Agents",
    singularLabel: "Agent",
    icon: MousePointerClickIcon,
    href: (artifact, workspaceId) =>
      `/workspaces/${workspaceId}/agents/${artifact.id}`,
    invalidateQueries: (queryClient, workspaceId, artifact) => {
      queryClient.invalidateQueries({
        queryKey: ["agent-preset", workspaceId, artifact.id],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-presets", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-directory-items", workspaceId],
      })
      queryClient.invalidateQueries({
        queryKey: ["agent-preset-versions", workspaceId, artifact.id],
      })
    },
  },
  integration: {
    label: "Integrations",
    singularLabel: "Integration",
    icon: BlocksIcon,
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

/** Invalidate embedded artifact queries after an artifact stream event. */
export function invalidateArtifactQueries(
  queryClient: QueryClient,
  workspaceId: string,
  artifact: WorkspaceChatArtifact
): void {
  getArtifactConfig(artifact).invalidateQueries?.(
    queryClient,
    workspaceId,
    artifact
  )
}
