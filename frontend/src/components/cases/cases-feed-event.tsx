import {
  BracesIcon,
  ClockPlusIcon,
  EyeIcon,
  type LucideIcon,
  PaperclipIcon,
  PencilIcon,
  PencilLineIcon,
  PlusIcon,
  TrashIcon,
  UserIcon,
  UserXIcon,
} from "lucide-react"
import type {
  AssigneeChangedEventRead,
  AttachmentCreatedEventRead,
  AttachmentDeletedEventRead,
  CaseEventRead,
  ClosedEventRead,
  FieldChangedEventRead,
  PayloadChangedEventRead,
  PriorityChangedEventRead,
  ReopenedEventRead,
  SeverityChangedEventRead,
  StatusChangedEventRead,
  TaskAssigneeChangedEventRead,
  TaskCreatedEventRead,
  TaskDeletedEventRead,
  TaskPriorityChangedEventRead,
  TaskStatusChangedEventRead,
  TaskWorkflowChangedEventRead,
  UpdatedEventRead,
} from "@/client"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import { UserHoverCard } from "@/components/cases/case-panel-common"
import { InlineDotSeparator } from "@/components/separator"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import type { User } from "@/lib/auth"
import { cn } from "@/lib/utils"

export function EventIcon({
  icon: Icon,
  className = "text-muted-foreground",
}: {
  icon: LucideIcon
  className?: string
}) {
  return (
    <div className="bg-background py-[2px]">
      <div
        className={cn(
          "rounded-full border border-muted-foreground/70 bg-white p-px",
          className
        )}
      >
        <Icon className="stroke-inherit/5 size-3 scale-75" strokeWidth={3} />
      </div>
    </div>
  )
}

export function EventActor({ user }: { user: User }) {
  return (
    <UserHoverCard user={user}>
      <span className="inline-block cursor-pointer font-medium hover:underline">
        {user.getDisplayName()}
      </span>
    </UserHoverCard>
  )
}

function shortTimeAgo(date: Date): string {
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

export function EventCreatedAt({ createdAt }: { createdAt: string }) {
  const date = new Date(createdAt)
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
            <ClockPlusIcon className="size-3" />
            {shortTimeAgo(date)}
          </span>
        </TooltipTrigger>
        <TooltipContent>{date.toLocaleString()}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

export function EventUpdatedAt({ updatedAt }: { updatedAt: string }) {
  const date = new Date(updatedAt)
  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <span className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground">
            <PencilLineIcon className="size-3" />
            {shortTimeAgo(date)}
          </span>
        </TooltipTrigger>
        <TooltipContent>Updated: {date.toLocaleString()}</TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}

export function AssigneeChangedEvent({
  event,
  actor,
  userMap,
}: {
  event: AssigneeChangedEventRead
  actor: User
  userMap: Record<string, User>
}) {
  const assigneeId = event.new
  if (assigneeId) {
    if (assigneeId === actor.id) {
      return (
        <div className="flex items-center space-x-2 text-xs">
          <EventIcon icon={UserIcon} />
          <span>
            <EventActor user={actor} /> self-assigned the case
          </span>
        </div>
      )
    }
    return (
      <div className="flex items-center space-x-2 text-xs">
        <EventIcon icon={UserIcon} />
        <span>
          <EventActor user={actor} /> assigned the case to{" "}
          <EventActor user={userMap[assigneeId]} />
        </span>
      </div>
    )
  }
  return (
    <div className="flex items-center space-x-2 text-xs">
      <EventIcon icon={UserXIcon} />
      <span>
        <EventActor user={actor} /> unassigned the case
      </span>
    </div>
  )
}

export function StatusChangedEvent({
  event,
  actor,
}: {
  event: StatusChangedEventRead
  actor: User
}) {
  if (event.old === event.new) {
    return null
  }
  const oldStatus = STATUSES[event.old]
  const newStatus = STATUSES[event.new]

  return (
    <div className="flex items-center space-x-2 text-xs">
      <EventIcon icon={newStatus.icon} className={newStatus.color} />
      <span>
        <EventActor user={actor} /> changed status from {oldStatus.label} to{" "}
        {newStatus.label}
      </span>
    </div>
  )
}

export function PriorityChangedEvent({
  event,
  actor,
}: {
  event: PriorityChangedEventRead
  actor: User
}) {
  if (event.old === event.new) {
    return null
  }
  const oldSeverity = PRIORITIES[event.old]
  const newPriority = PRIORITIES[event.new]

  return (
    <div className="flex items-center space-x-2 text-xs">
      <EventIcon icon={newPriority.icon} className={newPriority.color} />
      <span>
        <EventActor user={actor} /> changed priority from {oldSeverity.label} to{" "}
        {newPriority.label}
      </span>
    </div>
  )
}

export function SeverityChangedEvent({
  event,
  actor,
}: {
  event: SeverityChangedEventRead
  actor: User
}) {
  if (event.old === event.new) {
    return null
  }
  const oldSeverity = SEVERITIES[event.old]
  const newSeverity = SEVERITIES[event.new]

  return (
    <div className="flex items-center space-x-2 text-xs">
      <EventIcon icon={newSeverity.icon} className={newSeverity.color} />
      <span>
        <EventActor user={actor} /> changed severity from {oldSeverity.label} to{" "}
        {newSeverity.label}
      </span>
    </div>
  )
}

export function CaseReopenedEvent({
  event,
  actor,
}: {
  event: ReopenedEventRead
  actor: User
}) {
  const newStatus = STATUSES[event.new]
  return (
    <div className="flex items-center space-x-2 text-xs">
      <EventIcon icon={newStatus.icon} className={newStatus.color} />
      <span>
        <EventActor user={actor} /> reopened the case as {newStatus.label}
      </span>
    </div>
  )
}

export function CaseViewedEvent({
  event: _event,
  actor,
}: {
  event: CaseEventRead
  actor: User
}) {
  return (
    <div className="flex items-center space-x-2 text-xs">
      <EventIcon icon={EyeIcon} />
      <span>
        <EventActor user={actor} /> viewed the case
      </span>
    </div>
  )
}

export function CaseClosedEvent({
  event,
  actor,
}: {
  event: ClosedEventRead
  actor: User
}) {
  const newStatus = STATUSES[event.new]
  return (
    <div className="flex items-center space-x-2 text-xs">
      <EventIcon icon={newStatus.icon} className={newStatus.color} />
      <span>
        <EventActor user={actor} /> closed the case
      </span>
    </div>
  )
}

export function CaseUpdatedEvent({
  event,
  actor,
}: {
  event: UpdatedEventRead
  actor: User
}) {
  switch (event.field) {
    case "summary":
      return (
        <div className="flex items-center space-x-2 text-xs">
          <EventIcon icon={PencilIcon} />
          <span>
            <EventActor user={actor} />{" "}
            <TooltipProvider>
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="cursor-default">
                    {event.new
                      ? `changed summary to ${event.new}`
                      : "removed summary"}
                  </span>
                </TooltipTrigger>
                {event.old && (
                  <TooltipContent>
                    <p>Previously: {event.old}</p>
                  </TooltipContent>
                )}
              </Tooltip>
            </TooltipProvider>
          </span>
        </div>
      )
    default:
      return null
  }
}

export function FieldsChangedEvent({
  event,
  actor,
}: {
  event: FieldChangedEventRead
  actor: User
}) {
  return (
    <TooltipProvider>
      <div className="flex items-center space-x-2 text-xs">
        <EventIcon icon={BracesIcon} />
        <div className="flex flex-wrap items-center gap-1">
          <span>
            <EventActor user={actor} /> changed fields
          </span>
          {event.changes.map(({ field, old, new: newVal }) => (
            <>
              <InlineDotSeparator />
              <Tooltip>
                <TooltipTrigger asChild>
                  <span className="max-w-32 truncate text-xs hover:cursor-default hover:underline">
                    {field}
                  </span>
                </TooltipTrigger>
                <TooltipContent>
                  <span className="flex items-center gap-1">
                    {!!old && (
                      <>
                        <span>{JSON.stringify(old)}</span>
                        <span>→</span>
                      </>
                    )}
                    {!!newVal && <span>{JSON.stringify(newVal)}</span>}
                  </span>
                </TooltipContent>
              </Tooltip>
            </>
          ))}
        </div>
      </div>
    </TooltipProvider>
  )
}

export function AttachmentCreatedEvent({
  event,
  actor,
}: {
  event: AttachmentCreatedEventRead
  actor: User
}) {
  const formatFileSize = (bytes: number): string => {
    if (bytes === 0) return "0 Bytes"
    const k = 1024
    const sizes = ["Bytes", "KB", "MB", "GB"]
    const i = Math.floor(Math.log(bytes) / Math.log(k))
    return (
      Number.parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + " " + sizes[i]
    )
  }

  return (
    <div className="flex items-center space-x-2 text-xs">
      <EventIcon icon={PaperclipIcon} />
      <span>
        <EventActor user={actor} /> uploaded{" "}
        <TooltipProvider>
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="cursor-default font-medium hover:underline">
                {event.file_name}
              </span>
            </TooltipTrigger>
            <TooltipContent>
              <div className="space-y-1">
                <p>Size: {formatFileSize(event.size)}</p>
                <p>Type: {event.content_type}</p>
              </div>
            </TooltipContent>
          </Tooltip>
        </TooltipProvider>
      </span>
    </div>
  )
}

export function AttachmentDeletedEvent({
  event,
  actor,
}: {
  event: AttachmentDeletedEventRead
  actor: User
}) {
  return (
    <div className="flex items-center space-x-2 text-xs">
      <EventIcon icon={TrashIcon} className="text-red-600 bg-red-50" />
      <span>
        <EventActor user={actor} /> deleted{" "}
        <span className="font-medium">{event.file_name}</span>
      </span>
    </div>
  )
}

export function PayloadChangedEvent({
  event,
  actor,
}: {
  event: PayloadChangedEventRead
  actor: User
}) {
  return (
    <div className="flex items-center space-x-2 text-xs">
      <EventIcon icon={PencilIcon} />
      <span>
        <EventActor user={actor} /> updated the payload
      </span>
    </div>
  )
}

// Task events

export function TaskCreatedEvent({
  event,
  actor,
}: {
  event: TaskCreatedEventRead
  actor: User
}) {
  return (
    <div className="flex items-center space-x-2 text-xs">
      <EventIcon icon={PlusIcon} />
      <span>
        <EventActor user={actor} /> created the task{" "}
        <span className="font-medium">{event.title}</span>
      </span>
    </div>
  )
}

export function TaskDeletedEvent({
  event,
  actor,
}: {
  event: TaskDeletedEventRead
  actor: User
}) {
  return (
    <div className="flex items-center space-x-2 text-xs">
      <EventIcon icon={TrashIcon} className="text-red-600 bg-red-50" />
      <span>
        <EventActor user={actor} /> deleted the task
        {event.title ? (
          <span>
            {" "}
            <span className="font-medium">{event.title}</span>
          </span>
        ) : null}
      </span>
    </div>
  )
}

const TASK_STATUS_LABELS: Record<
  "todo" | "in_progress" | "completed" | "blocked",
  string
> = {
  todo: "To Do",
  in_progress: "In Progress",
  completed: "Completed",
  blocked: "Blocked",
}

export function TaskStatusChangedEvent({
  event,
  actor,
}: {
  event: TaskStatusChangedEventRead
  actor: User
}) {
  if (event.old === event.new) return null
  const oldStatus = TASK_STATUS_LABELS[event.old]
  const newStatus = TASK_STATUS_LABELS[event.new]
  return (
    <div className="flex items-center space-x-2 text-xs">
      <EventIcon icon={PencilIcon} />
      <span>
        <EventActor user={actor} /> changed{" "}
        <span className="font-medium max-w-32 inline-block truncate align-bottom">
          {event.title}
        </span>{" "}
        status: {oldStatus} → {newStatus}
      </span>
    </div>
  )
}

export function TaskAssigneeChangedEvent({
  event,
  actor,
  userMap,
}: {
  event: TaskAssigneeChangedEventRead
  actor: User
  userMap: Record<string, User>
}) {
  const oldAssignee = event.old ? userMap[event.old] : null
  const newAssignee = event.new ? userMap[event.new] : null

  return (
    <div className="flex items-center space-x-2 text-xs">
      <EventIcon icon={PencilIcon} />
      <span>
        <EventActor user={actor} /> {newAssignee ? "assigned" : "unassigned"}{" "}
        <span className="font-medium max-w-32 inline-block truncate align-bottom">
          {event.title}
        </span>{" "}
        {newAssignee ? (
          <>
            to <EventActor user={newAssignee} />
          </>
        ) : oldAssignee ? (
          <>
            from <EventActor user={oldAssignee} />
          </>
        ) : null}
      </span>
    </div>
  )
}

export function TaskPriorityChangedEvent({
  event,
  actor,
}: {
  event: TaskPriorityChangedEventRead
  actor: User
}) {
  if (event.old === event.new) return null
  const oldPriority = PRIORITIES[event.old]
  const newPriority = PRIORITIES[event.new]
  return (
    <div className="flex items-center space-x-2 text-xs">
      <EventIcon icon={PencilIcon} />
      <span>
        <EventActor user={actor} /> changed{" "}
        <span className="font-medium max-w-32 inline-block truncate align-bottom">
          {event.title}
        </span>{" "}
        priority: {oldPriority.label} → {newPriority.label}
      </span>
    </div>
  )
}

export function TaskWorkflowChangedEvent({
  event,
  actor,
}: {
  event: TaskWorkflowChangedEventRead
  actor: User
}) {
  if (event.old === event.new) return null
  const hasOld = !!event.old
  const hasNew = !!event.new

  if (!hasOld && hasNew) {
    return (
      <div className="flex items-center space-x-2 text-xs">
        <EventIcon icon={PencilIcon} />
        <span>
          <EventActor user={actor} /> linked workflow to{" "}
          <span className="font-medium max-w-32 inline-block truncate align-bottom">
            {event.title}
          </span>
        </span>
      </div>
    )
  }

  if (hasOld && !hasNew) {
    return (
      <div className="flex items-center space-x-2 text-xs">
        <EventIcon icon={PencilIcon} />
        <span>
          <EventActor user={actor} /> removed workflow from{" "}
          <span className="font-medium max-w-32 inline-block truncate align-bottom">
            {event.title}
          </span>
        </span>
      </div>
    )
  }

  return (
    <div className="flex items-center space-x-2 text-xs">
      <EventIcon icon={PencilIcon} />
      <span>
        <EventActor user={actor} /> changed workflow on{" "}
        <span className="font-medium max-w-32 inline-block truncate align-bottom">
          {event.title}
        </span>
      </span>
    </div>
  )
}
