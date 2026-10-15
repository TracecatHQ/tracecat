"use client"

import { WorkflowIcon } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { AgentWorkflowRef } from "@/lib/agent-catalog-filters"

const WORKFLOW_BADGE_CAP = 2

/** Badges for the workflows that run an agent, with overflow as `+N`. */
export function AgentWorkflowBadges({
  workflows,
}: {
  workflows: AgentWorkflowRef[]
}) {
  if (workflows.length === 0) return null
  const overflow = workflows.slice(WORKFLOW_BADGE_CAP)

  return (
    <div
      data-testid="workflow-badges"
      className="flex shrink-0 items-center gap-1"
    >
      {workflows.slice(0, WORKFLOW_BADGE_CAP).map((workflow) => (
        <Badge
          key={workflow.id}
          variant="secondary"
          className="h-5 max-w-[160px] gap-1 px-2 text-[10px] font-normal"
        >
          <WorkflowIcon className="size-3 shrink-0" />
          <span title={workflow.title} className="truncate">
            {workflow.title}
          </span>
        </Badge>
      ))}
      {overflow.length > 0 ? (
        <Tooltip>
          <TooltipTrigger asChild>
            <Badge
              variant="secondary"
              className="h-5 cursor-default px-2 text-[10px] font-normal"
            >
              +{overflow.length}
            </Badge>
          </TooltipTrigger>
          <TooltipContent>
            {overflow.map((workflow) => workflow.title).join(", ")}
          </TooltipContent>
        </Tooltip>
      ) : null}
    </div>
  )
}
