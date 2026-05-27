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
}

/** Mission Control artifact panel for streamed artifact tabs and content. */
export function ArtifactPanel({
  artifacts,
  activeArtifactKey,
  setActiveArtifactKey,
  closeArtifact,
  workspaceId,
  className,
}: ArtifactPanelProps) {
  const activeArtifact =
    artifacts.find((artifact) => artifactKey(artifact) === activeArtifactKey) ??
    artifacts[0]

  if (!activeArtifact) {
    return null
  }

  return (
    <aside
      className={cn(
        "flex min-h-0 w-full shrink-0 flex-col overflow-hidden border-t bg-background xl:h-full xl:w-1/2 xl:min-w-[360px] xl:border-l xl:border-t-0",
        className
      )}
    >
      <ArtifactTabs
        artifacts={artifacts}
        activeArtifact={activeArtifact}
        activeArtifactKey={activeArtifactKey}
        setActiveArtifactKey={setActiveArtifactKey}
        closeArtifact={closeArtifact}
        workspaceId={workspaceId}
      />
      <div className="min-h-0 flex-1 overflow-hidden">
        <ArtifactContent artifact={activeArtifact} workspaceId={workspaceId} />
      </div>
    </aside>
  )
}
