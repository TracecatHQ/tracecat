import {
  AnvilIcon,
  FlaskConicalIcon,
  MessageSquareIcon,
  SquareStackIcon,
  WorkflowIcon,
} from "lucide-react"
import { Badge } from "@/components/ui/badge"
import type { AgentSessionWithStatus } from "@/lib/agents"
import { cn } from "@/lib/utils"

interface ActivityItemProps {
  session: AgentSessionWithStatus
  isSelected: boolean
  onClick: () => void
}

function formatTimeAgo(dateStr: string): string {
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const diffMins = Math.floor(diffMs / 60000)
  const diffHours = Math.floor(diffMs / 3600000)
  const diffDays = Math.floor(diffMs / 86400000)
  const diffWeeks = Math.floor(diffDays / 7)
  const diffMonths = Math.floor(diffDays / 30)

  if (diffMins < 1) return "now"
  if (diffMins < 60) return `${diffMins}m`
  if (diffHours < 24) return `${diffHours}h`
  if (diffDays < 7) return `${diffDays}d`
  if (diffDays < 30) return `${diffWeeks}w`
  return `${diffMonths}mo`
}

type SourceType = "workflow" | "case" | "chat" | "test" | "assistant"

interface SourceConfig {
  label: string
  icon: React.ComponentType<{ className?: string }>
}

const SOURCE_CONFIGS: Record<SourceType, SourceConfig> = {
  workflow: {
    label: "Workflow",
    icon: WorkflowIcon,
  },
  case: {
    label: "Case",
    icon: SquareStackIcon,
  },
  chat: {
    label: "Chat",
    icon: MessageSquareIcon,
  },
  test: {
    label: "Test",
    icon: FlaskConicalIcon,
  },
  assistant: {
    label: "Build",
    icon: AnvilIcon,
  },
}

function getSourceType(entityType: string): SourceType {
  switch (entityType) {
    case "workflow":
      return "workflow"
    case "case":
      return "case"
    case "agent_preset":
      return "test"
    case "agent_preset_builder":
      return "assistant"
    case "copilot":
    default:
      return "chat"
  }
}

export function ActivityItem({
  session,
  isSelected,
  onClick,
}: ActivityItemProps) {
  // Display name: prefer workflow alias, then parent workflow title, then session title
  const displayName =
    session.parent_workflow?.alias ||
    session.parent_workflow?.title ||
    session.title

  const sourceType = getSourceType(session.entity_type)
  const sourceConfig = SOURCE_CONFIGS[sourceType]
  const SourceIcon = sourceConfig.icon

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "flex w-full items-center gap-3 py-2 pl-6 pr-6 text-left transition-colors",
        "hover:bg-muted/50",
        isSelected && "bg-muted"
      )}
    >
      {/* Agent name + badge */}
      <div className="flex min-w-0 flex-1 items-center gap-4">
        <span className="truncate text-sm">{displayName}</span>
        <Badge
          variant="secondary"
          className="shrink-0 gap-1 text-[10px] px-1.5 py-0 h-5 font-normal"
        >
          <SourceIcon className="size-3" />
          {sourceConfig.label}
        </Badge>
      </div>

      {/* Time */}
      <span className="shrink-0 text-xs text-muted-foreground">
        {formatTimeAgo(session.updated_at)}
      </span>
    </button>
  )
}
