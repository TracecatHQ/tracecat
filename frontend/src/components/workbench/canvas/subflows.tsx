import React from "react"
import Link from "next/link"
import { useWorkflowBuilder } from "@/providers/builder"
import { AlertTriangleIcon, SquareArrowOutUpRightIcon } from "lucide-react"

import { useWorkflowManager } from "@/lib/hooks"
import { Badge } from "@/components/ui/badge"

export function SubflowLink({
  workspaceId,
  subflowId,
  subflowAlias,
}: {
  workspaceId: string
  subflowId?: string
  subflowAlias?: string
}) {
  const { workflows } = useWorkflowManager()
  const { setSelectedNodeId } = useWorkflowBuilder()
  const idFromAlias = workflows?.find((w) => w.alias === subflowAlias)?.id

  const handleClearSelection = () => {
    setSelectedNodeId(null)
  }

  const inner = () => {
    if (subflowId) {
      return (
        <Link
          href={`/workspaces/${workspaceId}/workflows/${subflowId}`}
          onClick={handleClearSelection}
        >
          <div className="flex items-center gap-1">
            <span className="font-normal">Open workflow</span>
            <SquareArrowOutUpRightIcon className="size-3" />
          </div>
        </Link>
      )
    }
    if (subflowAlias) {
      if (!idFromAlias) {
        return (
          <div className="flex items-center gap-1">
            <span className="font-normal">Cannot find workflow by alias</span>
            <AlertTriangleIcon className="size-3 text-red-500" />
          </div>
        )
      }
      return (
        <div className="flex items-center gap-1">
          <Link
            href={`/workspaces/${workspaceId}/workflows/${idFromAlias}`}
            onClick={handleClearSelection}
          >
            <div className="flex items-center gap-1">
              <span className="font-mono font-normal tracking-tighter text-foreground/80">
                {subflowAlias}
              </span>
              <SquareArrowOutUpRightIcon className="size-3" />
            </div>
          </Link>
        </div>
      )
    }
    return <span className="font-normal">Missing identifier</span>
  }
  return (
    <div className="flex justify-end">
      <Badge
        variant="outline"
        className="text-foreground/70 hover:cursor-pointer hover:bg-muted-foreground/5"
      >
        {inner()}
      </Badge>
    </div>
  )
}
