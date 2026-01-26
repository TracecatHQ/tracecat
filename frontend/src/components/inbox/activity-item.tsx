import {
  AnvilIcon,
  FlaskConicalIcon,
  MessageSquareIcon,
  SquareStackIcon,
  WorkflowIcon,
} from "lucide-react"
import {
  EventCreatedAt,
  EventUpdatedAt,
} from "@/components/cases/cases-feed-event"
import { Badge } from "@/components/ui/badge"
import type { AgentSessionWithStatus } from "@/lib/agents"
import { cn } from "@/lib/utils"

interface ActivityItemProps {
  session: AgentSessionWithStatus
  isSelected: boolean
  onClick: () => void
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
        // Use negative margins to extend hover to full width
        "-ml-[18px] flex w-[calc(100%+18px)] items-center gap-3 py-2 pl-3 pr-3 text-left transition-colors",
        "hover:bg-muted/50",
        isSelected && "bg-muted"
      )}
    >
      {/* Spacer to align with accordion chevron (h-7 w-7) */}
      <div className="h-7 w-7 shrink-0" />

      {/* Agent name + badge */}
      <div className="flex min-w-0 flex-1 items-center gap-2">
        <span className="truncate text-xs">{displayName}</span>
        <Badge
          variant="secondary"
          className="shrink-0 gap-1 text-[10px] px-1.5 py-0 h-5 font-normal"
        >
          <SourceIcon className="size-3" />
          {sourceConfig.label}
        </Badge>
      </div>

      {/* Timestamps */}
      <div className="flex shrink-0 items-center gap-2">
        <EventCreatedAt createdAt={session.created_at} />
        <EventUpdatedAt updatedAt={session.updated_at} />
      </div>
    </button>
  )
}
