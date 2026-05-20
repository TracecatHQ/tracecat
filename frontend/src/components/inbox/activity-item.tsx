import {
  AnvilIcon,
  FlaskConicalIcon,
  LayersIcon,
  MessageSquareIcon,
  Trash2Icon,
  WorkflowIcon,
} from "lucide-react"
import { useState } from "react"
import {
  EventCreatedAt,
  EventUpdatedAt,
} from "@/components/cases/cases-feed-event"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import type { InboxSessionItem } from "@/lib/agents"
import { cn } from "@/lib/utils"

interface ActivityItemProps {
  session: InboxSessionItem
  isSelected: boolean
  isDeleting?: boolean
  onClick: () => void
  onDelete?: () => void
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
    icon: LayersIcon,
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
  isDeleting,
  onClick,
  onDelete,
}: ActivityItemProps) {
  const [confirmOpen, setConfirmOpen] = useState(false)

  const displayName =
    session.parent_workflow?.alias ||
    session.parent_workflow?.title ||
    session.title

  const sourceType = getSourceType(session.entity_type)
  const sourceConfig = SOURCE_CONFIGS[sourceType]
  const SourceIcon = sourceConfig.icon

  return (
    <>
      <div
        className={cn(
          "-ml-[18px] group/item flex w-[calc(100%+18px)] items-center gap-3 py-2 pl-3 pr-3 transition-colors",
          "hover:bg-muted/50",
          isSelected && "bg-muted"
        )}
      >
        <button
          type="button"
          onClick={onClick}
          className="flex min-w-0 flex-1 items-center gap-3 text-left"
        >
          <div className="h-7 w-7 shrink-0" />
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
          <div className="flex shrink-0 items-center gap-2">
            <EventCreatedAt createdAt={session.created_at} />
            <EventUpdatedAt updatedAt={session.updated_at} />
          </div>
        </button>

        {onDelete && (
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              setConfirmOpen(true)
            }}
            disabled={isDeleting}
            className={cn(
              "flex size-6 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors",
              "opacity-0 group-hover/item:opacity-100",
              "hover:bg-destructive/10 hover:text-destructive",
              isDeleting && "cursor-not-allowed opacity-50"
            )}
            aria-label="Delete approval"
          >
            <Trash2Icon className="size-3.5" />
          </button>
        )}
      </div>

      <AlertDialog open={confirmOpen} onOpenChange={setConfirmOpen}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete approval</AlertDialogTitle>
            <AlertDialogDescription>
              This will deny all pending tool calls and remove this approval
              from the inbox.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={() => {
                setConfirmOpen(false)
                onDelete?.()
              }}
            >
              Delete
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
