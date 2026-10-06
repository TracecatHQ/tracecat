"use client"

import { FolderIcon, StarIcon } from "lucide-react"
import Link from "next/link"
import { agentFoldersGetFolder } from "@/client"
import { Badge } from "@/components/ui/badge"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { useAgentPreset } from "@/hooks/use-agent-presets"
import { useQuery } from "@/lib/query"
import { useWorkspaceId } from "@/providers/workspace-id"

/** Link to the workspace default, with a preview independent of the current folder. */
export function WorkspaceDefaultAgentBadge({
  presetId,
  organizationEnabled,
}: {
  presetId: string
  organizationEnabled: boolean
}) {
  const workspaceId = useWorkspaceId()
  const { preset, presetError } = useAgentPreset(workspaceId, presetId)
  const folderId = preset?.folder_id
  const { data: folder } = useQuery({
    queryKey: ["agent-folders", workspaceId, folderId],
    queryFn: () => {
      if (!folderId) throw new Error("Folder ID is required")
      return agentFoldersGetFolder({ workspaceId, folderId })
    },
    enabled: organizationEnabled && Boolean(folderId),
  })

  if (!preset || presetError) return null

  return (
    <HoverCard>
      <HoverCardTrigger asChild>
        <Link
          href={`/workspaces/${workspaceId}/agents/${preset.id}`}
          aria-label={`Default agent: ${preset.name}`}
          className="ml-auto rounded-md focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring"
        >
          <Badge
            variant="outline"
            className="h-6 max-w-64 gap-1.5 px-2 text-xs font-normal hover:bg-muted/50"
          >
            <StarIcon className="size-3 shrink-0" />
            <span className="truncate">Default: {preset.name}</span>
          </Badge>
        </Link>
      </HoverCardTrigger>
      <HoverCardContent align="end" className="w-80 space-y-2 shadow-none">
        <p className="text-xs text-muted-foreground">Workspace default agent</p>
        <p className="break-words text-sm font-medium">{preset.name}</p>
        <p className="whitespace-pre-wrap break-words text-xs text-muted-foreground">
          {preset.description || "No description"}
        </p>
        {folder && folder.path !== "/" ? (
          <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <FolderIcon className="size-3.5 shrink-0" />
            <span className="break-all">{folder.path}</span>
          </div>
        ) : null}
      </HoverCardContent>
    </HoverCard>
  )
}
