"use client"

import { ArtifactContent } from "@/components/workspace-chat/artifacts/artifact-content"
import { ArtifactTabs } from "@/components/workspace-chat/artifacts/artifact-tabs"
import { cn } from "@/lib/utils"
import {
  type ArtifactType,
  artifactKey,
  type WorkspaceChatArtifact,
} from "@/types/workspace-chat-artifacts"

export interface ArtifactPanelProps {
  artifacts: WorkspaceChatArtifact[]
  activeArtifactKey: string | null
  setActiveArtifactKey: (key: string | null) => void
  closeArtifact: (type: ArtifactType, id: string) => void
  workspaceId: string
  artifactTab: string | null
  setArtifactTab: (tab: string | null) => void
  className?: string
  /** Triggered when the user manually collapses the panel. */
  onCollapse: () => void
}

/**
 * Workspace chat artifact panel.
 *
 * Parent layouts own sizing and collapse state. This shell fills the artifact
 * slot and renders the active artifact when one exists.
 */
export function ArtifactPanel({
  artifacts,
  activeArtifactKey,
  setActiveArtifactKey,
  closeArtifact,
  workspaceId,
  artifactTab,
  setArtifactTab,
  className,
  onCollapse,
}: ArtifactPanelProps) {
  const activeArtifact =
    artifacts.find((artifact) => artifactKey(artifact) === activeArtifactKey) ??
    artifacts[0]

  return (
    <aside
      className={cn(
        "flex size-full min-h-0 min-w-0 flex-col overflow-hidden bg-background",
        className
      )}
    >
      {activeArtifact ? (
        <>
          <ArtifactTabs
            artifacts={artifacts}
            activeArtifact={activeArtifact}
            activeArtifactKey={activeArtifactKey}
            setActiveArtifactKey={setActiveArtifactKey}
            closeArtifact={closeArtifact}
            workspaceId={workspaceId}
            onCollapse={onCollapse}
          />
          <div className="min-h-0 flex-1 overflow-hidden">
            <ArtifactContent
              artifact={activeArtifact}
              workspaceId={workspaceId}
              activeTab={artifactTab}
              onTabChange={setArtifactTab}
            />
          </div>
        </>
      ) : null}
    </aside>
  )
}
