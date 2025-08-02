import {
  BracesIcon,
  type LucideIcon,
  PaperclipIcon,
  PencilIcon,
  TrashIcon,
  UserIcon,
  UserXIcon,
} from "lucide-react"
import type {
  AssigneeChangedEventRead,
  AttachmentCreatedEventRead,
  AttachmentDeletedEventRead,
  ClosedEventRead,
  FieldChangedEventRead,
  PayloadChangedEventRead,
  PriorityChangedEventRead,
  ReopenedEventRead,
  SeverityChangedEventRead,
  StatusChangedEventRead,
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
