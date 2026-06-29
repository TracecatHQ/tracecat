"use client"

import { ExternalLink } from "lucide-react"
import dynamic from "next/dynamic"
import Link from "next/link"
import { useEffect, useMemo } from "react"
import { AgentPresetArtifactView } from "@/components/agents/agent-presets-builder"
import { CasePanelView } from "@/components/cases/case-panel-view"
import { AlertNotification } from "@/components/notifications"
import { TablePanelProvider } from "@/components/tables/table-panel-context"
import { TableSelectionProvider } from "@/components/tables/table-selection-context"
import { DatabaseTable } from "@/components/tables/table-view"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { Skeleton } from "@/components/ui/skeleton"
import {
  getArtifactConfig,
  getArtifactHref,
} from "@/components/workspace-chat/artifacts/artifact-registry"
import { ArtifactIcon } from "@/components/workspace-chat/artifacts/artifact-tabs"
import { useAgentPreset } from "@/hooks/use-agent-presets"
import { useGetTable } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import type { WorkspaceChatArtifact } from "@/types/workspace-chat-artifacts"

// Lazy-loaded: this pulls in React Flow and the full workflow builder, which
// would otherwise be statically compiled into the workspace-chat route and
// bloat its bundle/dev compile. Loaded on demand when a workflow artifact opens.
const WorkflowArtifactView = dynamic(
  () =>
    import("@/components/workspace-chat/artifacts/workflow-artifact-view").then(
      (mod) => mod.WorkflowArtifactView
    ),
  {
    ssr: false,
    loading: () => (
      <div className="flex h-full flex-col gap-3 p-4">
        <Skeleton className="h-5 w-1/2" />
        <Skeleton className="min-h-0 w-full flex-1" />
      </div>
    ),
  }
)

const CASE_ARTIFACT_TABS = new Set([
  "comments",
  "activity",
  "attachments",
  "rows",
  "payload",
])
const AGENT_ARTIFACT_TABS = new Set([
  "live-chat",
  "assistant",
  "configuration",
  "subagents",
  "skills",
  "channels",
  "structured-output",
  "versions",
])

export interface ArtifactContentProps {
  artifact: WorkspaceChatArtifact
  workspaceId: string
  activeTab: string | null
  onTabChange: (tab: string | null) => void
}

/** Render the active artifact content with type-specific embedded views. */
export function ArtifactContent({
  artifact,
  workspaceId,
  activeTab,
  onTabChange,
}: ArtifactContentProps) {
  const normalizedTab = useMemo(
    () => normalizeArtifactTab(artifact, activeTab),
    [artifact, activeTab]
  )

  useEffect(() => {
    if (activeTab !== null && normalizedTab === null) {
      onTabChange(null)
    }
  }, [activeTab, normalizedTab, onTabChange])

  switch (artifact.type) {
    case "case":
      return (
        <CasePanelView
          caseId={artifact.id}
          embedded
          initialTab={normalizedTab}
          onTabChange={onTabChange}
        />
      )
    case "table":
      return (
        <EmbeddedTableArtifact artifact={artifact} workspaceId={workspaceId} />
      )
    case "workflow":
      return (
        <WorkflowArtifactView
          workflowId={artifact.id}
          workspaceId={workspaceId}
        />
      )
    case "agent":
      return (
        <EmbeddedAgentArtifact
          artifact={artifact}
          workspaceId={workspaceId}
          activeTab={normalizedTab}
          onTabChange={onTabChange}
        />
      )
    default:
      return <ArtifactSummary artifact={artifact} workspaceId={workspaceId} />
  }
}

function normalizeArtifactTab(
  artifact: WorkspaceChatArtifact,
  tab: string | null
): string | null {
  if (!tab) {
    return null
  }

  switch (artifact.type) {
    case "case":
      return CASE_ARTIFACT_TABS.has(tab) ? tab : null
    case "agent":
      return AGENT_ARTIFACT_TABS.has(tab) ? tab : null
    default:
      return null
  }
}

function EmbeddedTableArtifact({
  artifact,
  workspaceId,
}: {
  artifact: Extract<WorkspaceChatArtifact, { type: "table" }>
  workspaceId: string
}) {
  const { table, tableIsLoading, tableError } = useGetTable({
    tableId: artifact.id,
    workspaceId,
  })

  if (tableIsLoading) {
    return (
      <div className="flex h-full flex-col gap-3 p-4">
        <Skeleton className="h-5 w-1/2" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-full" />
        <Skeleton className="h-8 w-2/3" />
      </div>
    )
  }

  if (tableError || !table) {
    return (
      <div className="p-4">
        <AlertNotification
          message={tableError?.message ?? "Error loading table"}
          variant="error"
        />
      </div>
    )
  }

  return (
    <div className="size-full overflow-hidden">
      <TableSelectionProvider>
        <TablePanelProvider>
          <DatabaseTable table={table} />
        </TablePanelProvider>
      </TableSelectionProvider>
    </div>
  )
}

function EmbeddedAgentArtifact({
  artifact,
  workspaceId,
  activeTab,
  onTabChange,
}: {
  artifact: Extract<WorkspaceChatArtifact, { type: "agent" }>
  workspaceId: string
  activeTab: string | null
  onTabChange: (tab: string | null) => void
}) {
  const { preset, presetIsLoading, presetError } = useAgentPreset(
    workspaceId,
    artifact.id
  )

  if (presetIsLoading) {
    return (
      <div className="flex h-full flex-col gap-3 p-4">
        <Skeleton className="h-5 w-1/2" />
        <Skeleton className="h-4 w-2/3" />
        <Skeleton className="h-16 w-full" />
      </div>
    )
  }

  if (presetError || !preset) {
    return (
      <div className="p-4">
        <AlertNotification
          message={presetError?.message ?? "Error loading agent"}
          variant="error"
        />
      </div>
    )
  }

  return (
    <AgentPresetArtifactView
      preset={preset}
      workspaceId={workspaceId}
      initialTab={activeTab}
      onTabChange={onTabChange}
    />
  )
}

function ArtifactSummary({
  artifact,
  workspaceId,
}: {
  artifact: WorkspaceChatArtifact
  workspaceId: string
}) {
  const config = getArtifactConfig(artifact)
  const href = getArtifactHref(artifact, workspaceId)

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div className="shrink-0 border-b px-4 py-3">
        <div className="flex items-center gap-2">
          <ArtifactIcon artifact={artifact} icon={config.icon} />
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{artifact.title}</p>
            <p className="text-xs text-muted-foreground">
              {config.singularLabel}
            </p>
          </div>
        </div>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-4 p-4 text-sm">
          <ArtifactFields artifact={artifact} />
          {href ? (
            <Button size="sm" variant="outline" className="h-8 gap-1.5" asChild>
              <Link href={href}>
                <ExternalLink className="size-3.5" />
                Open full view
              </Link>
            </Button>
          ) : null}
        </div>
      </ScrollArea>
    </div>
  )
}

function ArtifactFields({ artifact }: { artifact: WorkspaceChatArtifact }) {
  switch (artifact.type) {
    case "run":
      return (
        <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-2">
          <ArtifactField label="Status" value={artifact.status} />
          <ArtifactField label="Started" value={artifact.startedAt} />
          <ArtifactField label="Run ID" value={artifact.id} monospace />
        </dl>
      )
    case "generic":
      return (
        <pre className="max-h-96 overflow-auto rounded-sm border bg-muted/30 p-3 text-xs">
          {JSON.stringify(artifact.data ?? {}, null, 2)}
        </pre>
      )
    default:
      return (
        <dl className="grid grid-cols-[6rem_1fr] gap-x-3 gap-y-2">
          <ArtifactField label="ID" value={artifact.id} monospace />
        </dl>
      )
  }
}

function ArtifactField({
  label,
  value,
  monospace = false,
}: {
  label: string
  value: string | number
  monospace?: boolean
}) {
  return (
    <>
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd
        className={cn("min-w-0 break-words text-xs", monospace && "font-mono")}
      >
        {monospace ? String(value) : String(value).replaceAll("_", " ")}
      </dd>
    </>
  )
}
