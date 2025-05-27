import {
  AssigneeChangedEventRead,
  ClosedEventRead,
  FieldChangedEventRead,
  PriorityChangedEventRead,
  ReopenedEventRead,
  SeverityChangedEventRead,
  StatusChangedEventRead,
  UpdatedEventRead,
} from "@/client"
import {
  BracesIcon,
  LucideIcon,
  PencilIcon,
  UserIcon,
  UserXIcon,
} from "lucide-react"

import { User } from "@/lib/auth"
import { cn } from "@/lib/utils"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import {
  PRIORITIES,
  SEVERITIES,
  STATUSES,
} from "@/components/cases/case-categories"
import {
  InlineDotSeparator,
  UserHoverCard,
} from "@/components/cases/case-panel-common"

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
      <div className="flex items-center space-x-1 text-xs">
        <EventIcon icon={UserIcon} />
        <span>
          <EventActor user={actor} /> assigned the case to
        </span>
        <EventActor user={userMap[assigneeId]} />
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
          <TooltipProvider>
            <Tooltip>
              <EventActor user={actor} />{" "}
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
    <div className="flex items-center space-x-2 text-xs">
      <EventIcon icon={BracesIcon} />
      <span>
        <EventActor user={actor} /> changed fields
      </span>
      <InlineDotSeparator />
      <div>
        {event.changes.map(({ field, old, new: newVal }) => {
          return (
            <FieldChangeDiff key={field} field={field} old={old} new={newVal} />
          )
        })}
      </div>
    </div>
  )
}

function FieldChangeDiff<T = unknown>({
  field,
  old: oldVal,
  new: newVal,
}: {
  field: string
  old: T
  new: T
}) {
  if (oldVal === newVal) {
    return null
  }

  return (
    <span key={field} className="flex max-w-sm items-center space-x-2 text-xs">
      <span className="font-medium">{field}:</span>
      {!!oldVal && (
        <>
          <span className="truncate text-xs">
            <span className="text-muted-foreground line-through">
              {JSON.stringify(oldVal)}
            </span>
          </span>
          <span className="text-muted-foreground">→</span>
        </>
      )}
      <span className="truncate text-xs">
        {!newVal ? (
          <span className="text-muted-foreground/70">Unset</span>
        ) : (
          <span>{JSON.stringify(newVal)}</span>
        )}
      </span>
    </span>
  )
}
