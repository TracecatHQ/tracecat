import React from "react"

import { CasePriorityType, CaseStatusType } from "@/types/schemas"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Textarea } from "@/components/ui/textarea"
import { StatusBadge } from "@/components/badges"
import { priorities, statuses } from "@/components/cases/data/categories"
import UserAvatar from "@/components/user-avatar"

type User = {
  src: string
  name: string
}

/**
 *
 * Audit trail of events, sorted by time
 *
 * @param param0
 * @returns
 */
export function Timeline({
  items,
  className,
}: React.HTMLAttributes<HTMLUListElement> & {
  items: TimelineItemProps[]
}) {
  console.log("items", items)
  return (
    <div className={cn("pl-4", className)}>
      <ol className="relative space-y-8 border-s border-gray-200 p-4 dark:border-gray-700">
        {items.map((item, index) => (
          <TimelineItem key={index} {...item} />
        ))}
      </ol>
      <Textarea
        className="mt-4 min-h-20 text-xs"
        placeholder="Add a comment..."
      />
      <Button className="ml-auto mt-2 flex justify-end text-xs">
        Add Comment
      </Button>
    </div>
  )
}
export type TimelineAction =
  | "changed_status"
  | "changed_priority"
  | "added_comment"
  | "opened_case"
  | "closed_case"

export type TimelineItemProps = TimelineItemActivityProps & {
  className?: string
}

export function TimelineItem({
  className,
  ...activityProps
}: TimelineItemProps) {
  return (
    <li className={cn("ms-6 rounded-lg text-xs shadow-sm", className)}>
      <UserAvatar
        className="absolute -start-3 flex size-6 items-center justify-center ring-8 ring-white"
        src={activityProps.user.src}
        alt={activityProps.user.name}
      />
      <TimelineItemActivity {...activityProps} />
    </li>
  )
}
export interface TimelineItemActivityProps {
  user: User
  action: TimelineAction
  updatedAt: string
  detail?: string
  activity?: Record<string, any>
}
function TimelineItemActivity(props: TimelineItemActivityProps) {
  const { detail, ...rest } = props

  return (
    <div className="space-y-4 rounded-lg border border-gray-200 p-4 shadow-sm">
      <TimelineItemActivityHeader {...rest} />
      {detail && (
        <TimelineItemActivityDetail>{detail}</TimelineItemActivityDetail>
      )}
    </div>
  )
}

const getActivityDescription = ({
  user,
  action,
  activity,
}: TimelineItemActivityHeaderProps): React.ReactNode => {
  const name = <b>{user.name}</b>
  switch (action) {
    case "added_comment":
      return <span>{name} added a comment to the case.</span>
    case "opened_case":
      return <span>{name} opened the case.</span>
    case "closed_case":
      return <span>{name} closed the case.</span>
    case "changed_status":
      const statusVal = activity?.status as CaseStatusType
      const statusObj = statuses.find((s) => s.value === statusVal)
      const newStatus = (
        <StatusBadge status={statusObj?.value} className="ml-1">
          {statusObj?.icon && (
            <statusObj.icon className="h-3 w-3 text-foreground" />
          )}
          {statusVal}
        </StatusBadge>
      )
      return (
        <span>
          {name} updated the case status to {newStatus}.
        </span>
      )
    case "changed_priority":
      const priorityVal = activity?.priority as CasePriorityType
      const priorityObj = priorities.find((p) => p.value === priorityVal)
      const newPriority = (
        <StatusBadge status={priorityObj?.value} className="ml-1">
          {priorityObj?.icon && (
            <priorityObj.icon className="h-3 w-3 text-muted-foreground" />
          )}
          {priorityObj?.label}
        </StatusBadge>
      )
      return (
        <span>
          {name} updated the case priority to {newPriority}.
        </span>
      )
    default:
      return null
  }
}

type TimelineItemActivityHeaderProps = Omit<
  TimelineItemActivityProps,
  "detail"
> & {
  className?: string
}

function TimelineItemActivityHeader(props: TimelineItemActivityHeaderProps) {
  const activityDescription = getActivityDescription(props)
  const { updatedAt, className } = props
  return (
    <div className={cn("items-center justify-between sm:flex", className)}>
      <time className="mb-1 text-xs font-normal text-muted-foreground sm:order-last sm:mb-0">
        {updatedAt}
      </time>
      <div className="lex font-normal text-muted-foreground">
        {activityDescription}
      </div>
    </div>
  )
}

function TimelineItemActivityDetail({
  className,
  children,
}: React.HTMLAttributes<HTMLDivElement> & {
  children?: React.ReactNode
}) {
  return (
    <div
      className={cn(
        "rounded-lg border border-gray-200 bg-gray-50 p-3 text-xs font-normal italic text-muted-foreground dark:border-gray-500 dark:bg-gray-600 dark:text-gray-300",
        className
      )}
    >
      {children}
    </div>
  )
}
