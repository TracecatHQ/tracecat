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
  /** Whether the panel is currently collapsed (width 0). */
  isCollapsed: boolean
  /** Triggered when the user manually collapses the panel. */
  onCollapse: () => void
}

/**
 * Mission Control artifact panel.
 *
 * Stays mounted at all times and animates `width` between `0` and `w-1/2`
 * so the chat column never gets pushed off-center. The inner content is
 * skipped entirely when there are no artifacts so we don't pay rendering
 * cost while collapsed.
 */
export function ArtifactPanel({
  artifacts,
  activeArtifactKey,
  setActiveArtifactKey,
  closeArtifact,
  workspaceId,
  className,
  isCollapsed,
  onCollapse,
}: ArtifactPanelProps) {
  const activeArtifact =
    artifacts.find((artifact) => artifactKey(artifact) === activeArtifactKey) ??
    artifacts[0]

  return (
    <aside
      aria-hidden={isCollapsed}
      inert={isCollapsed}
      className={cn(
        "flex h-full min-h-0 shrink-0 flex-col overflow-hidden bg-background transition-[width,min-width,border-left-width] duration-200 ease-[cubic-bezier(0.25,0.1,0.25,1)]",
        isCollapsed ? "w-0 min-w-0 border-l-0" : "w-1/2 min-w-[360px] border-l",
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
