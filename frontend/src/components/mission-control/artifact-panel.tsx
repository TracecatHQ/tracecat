"use client"

import { ArtifactContent } from "@/components/mission-control/artifact-content"
import { ArtifactTabs } from "@/components/mission-control/artifact-tabs"
import { cn } from "@/lib/utils"
import {
  type ArtifactType,
  artifactKey,
  type MissionControlArtifact,
} from "@/types/mission-control"

export interface ArtifactPanelProps {
  artifacts: MissionControlArtifact[]
  activeArtifactKey: string | null
  setActiveArtifactKey: (key: string | null) => void
  closeArtifact: (type: ArtifactType, id: string) => void
  workspaceId: string
  className?: string
  /** Triggered when the user manually collapses the panel. */
  onCollapse: () => void
}

/**
 * Mission Control artifact panel.
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
            />
          </div>
        </>
      ) : null}
    </aside>
  )
}
