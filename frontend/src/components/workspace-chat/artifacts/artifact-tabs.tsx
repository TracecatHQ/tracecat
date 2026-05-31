"use client"

import { ExternalLink, PanelLeftIcon, X } from "lucide-react"
import Link from "next/link"
import { useEffect, useRef } from "react"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  type ArtifactIconComponent,
  getArtifactConfig,
  getArtifactHref,
} from "@/components/workspace-chat/artifacts/artifact-registry"
import { cn } from "@/lib/utils"
import {
  type ArtifactType,
  artifactKey,
  type WorkspaceChatArtifact,
} from "@/types/workspace-chat-artifacts"

export interface ArtifactTabsProps {
  artifacts: WorkspaceChatArtifact[]
  activeArtifact: WorkspaceChatArtifact
  activeArtifactKey: string | null
  setActiveArtifactKey: (key: string | null) => void
  closeArtifact: (type: ArtifactType, id: string) => void
  workspaceId: string
  onCollapse?: () => void
}

/** Horizontal artifact tab strip for the Chat panel. */
export function ArtifactTabs({
  artifacts,
  activeArtifact,
  activeArtifactKey,
  setActiveArtifactKey,
  closeArtifact,
  workspaceId,
  onCollapse,
}: ArtifactTabsProps) {
  const scrollNodeRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    const node = scrollNodeRef.current
    if (!node) {
      return
    }
    const handler = (event: WheelEvent) => {
      if (event.deltaY === 0) {
        return
      }
      node.scrollLeft += event.deltaY
      event.preventDefault()
    }
    node.addEventListener("wheel", handler, { passive: false })
    return () => node.removeEventListener("wheel", handler)
  }, [])

  useEffect(() => {
    const node = scrollNodeRef.current
    if (!node || !activeArtifactKey) {
      return
    }
    const tab = node.querySelector<HTMLElement>(
      `[data-artifact-tab-id="${CSS.escape(activeArtifactKey)}"]`
    )
    if (!tab) {
      return
    }

    const tabRect = tab.getBoundingClientRect()
    const nodeRect = node.getBoundingClientRect()
    const tabLeft = tabRect.left - nodeRect.left + node.scrollLeft
    const tabRight = tabLeft + tabRect.width
    const viewLeft = node.scrollLeft
    const viewRight = viewLeft + node.clientWidth
    if (tabLeft < viewLeft) {
      node.scrollTo({ left: tabLeft, behavior: "smooth" })
    } else if (tabRight > viewRight) {
      node.scrollTo({ left: tabRight - node.clientWidth, behavior: "smooth" })
    }
  }, [activeArtifactKey])

  return (
    <div className="flex h-10 shrink-0 items-center gap-1 border-b px-2">
      {onCollapse ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              size="sm"
              variant="ghost"
              className="size-7 p-0"
              onClick={onCollapse}
              aria-label="Hide artifacts"
            >
              <PanelLeftIcon className="size-3.5" />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">Hide artifacts</TooltipContent>
        </Tooltip>
      ) : null}
      <div
        ref={scrollNodeRef}
        className="flex min-w-0 flex-1 items-center gap-1 overflow-x-auto [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      >
        {artifacts.map((artifact) => (
          <ArtifactTab
            key={artifactKey(artifact)}
            artifact={artifact}
            active={artifactKey(artifact) === artifactKey(activeArtifact)}
            setActiveArtifactKey={setActiveArtifactKey}
            closeArtifact={closeArtifact}
          />
        ))}
      </div>
      <ArtifactOpenButton artifact={activeArtifact} workspaceId={workspaceId} />
    </div>
  )
}

function ArtifactTab({
  artifact,
  active,
  setActiveArtifactKey,
  closeArtifact,
}: {
  artifact: WorkspaceChatArtifact
  active: boolean
  setActiveArtifactKey: (key: string | null) => void
  closeArtifact: (type: ArtifactType, id: string) => void
}) {
  const key = artifactKey(artifact)
  const config = getArtifactConfig(artifact)
  const Icon = config.icon

  return (
    <div
      data-artifact-tab-id={key}
      className={cn(
        "group flex h-7 min-w-0 max-w-[min(14rem,100%)] shrink-0 items-center rounded-sm text-xs",
        active
          ? "bg-muted text-foreground"
          : "text-muted-foreground hover:bg-muted/70 hover:text-foreground"
      )}
    >
      <button
        type="button"
        onClick={() => setActiveArtifactKey(key)}
        className="flex h-full min-w-0 items-center gap-1.5 px-2"
        title={artifact.title}
      >
        <Icon className="size-3.5 shrink-0" />
        <span className="truncate">{artifact.title}</span>
      </button>
      <button
        type="button"
        className="mr-1 inline-flex rounded-sm p-0.5 text-muted-foreground opacity-0 hover:bg-background group-hover:opacity-100"
        aria-label={`Close ${artifact.title}`}
        onClick={() => closeArtifact(artifact.type, artifact.id)}
      >
        <X className="size-3" />
      </button>
    </div>
  )
}

export function ArtifactIcon({
  artifact,
  icon: Icon,
}: {
  artifact: WorkspaceChatArtifact
  icon: ArtifactIconComponent
}) {
  if (artifact.type === "workflow") {
    return (
      <span
        className="size-3 shrink-0 rounded-[3px] border"
        style={{ backgroundColor: artifact.color }}
      />
    )
  }

  return <Icon className="size-3.5 shrink-0" />
}

function ArtifactOpenButton({
  artifact,
  workspaceId,
}: {
  artifact: WorkspaceChatArtifact
  workspaceId: string
}) {
  const href = getArtifactHref(artifact, workspaceId)
  if (!href) {
    return (
      <Tooltip>
        <TooltipTrigger asChild>
          <span aria-label="No full view" className="inline-flex" tabIndex={0}>
            <Button
              aria-hidden="true"
              size="sm"
              variant="ghost"
              className="pointer-events-none size-7 p-0"
              disabled
            >
              <ExternalLink className="size-3.5" />
            </Button>
          </span>
        </TooltipTrigger>
        <TooltipContent side="bottom">No full view</TooltipContent>
      </Tooltip>
    )
  }

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <Button size="sm" variant="ghost" className="size-7 p-0" asChild>
          <Link href={href} aria-label={`Open ${artifact.title}`}>
            <ExternalLink className="size-3.5" />
          </Link>
        </Button>
      </TooltipTrigger>
      <TooltipContent side="bottom">Open full view</TooltipContent>
    </Tooltip>
  )
}
