import React from "react"
import { useUser } from "@clerk/nextjs"
import { zodResolver } from "@hookform/resolvers/zod"
import { ChatBubbleIcon } from "@radix-ui/react-icons"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { CaseEvent, CasePriorityType, CaseStatusType } from "@/types/schemas"
import { userDefaults } from "@/config/user"
import { useCaseEvents } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from "@/components/ui/form"
import { Textarea } from "@/components/ui/textarea"
import { StatusBadge } from "@/components/badges"
import { priorities, statuses } from "@/components/cases/data/categories"
import { CenteredSpinner } from "@/components/loading/spinner"
import NoContent from "@/components/no-content"
import { AlertNotification } from "@/components/notifications"
import UserAvatar from "@/components/user-avatar"

const timelineCommentFormSchema = z.object({
  comment: z.string().min(1, "Comment cannot be empty"),
})

export type TimelineCommentForm = z.infer<typeof timelineCommentFormSchema>

/**
 *
 * Audit trail of events, sorted by time
 *
 * @param param0
 * @returns
 */
export function Timeline({
  workflowId,
  caseId,
  className,
}: React.HTMLAttributes<HTMLUListElement> & {
  workflowId: string
  caseId: string
}) {
  const { user } = useUser()
  const {
    caseEvents,
    caseEventsIsLoading,
    caseEventsError,
    mutateCaseEventsAsync,
  } = useCaseEvents(workflowId, caseId)

  const methods = useForm<TimelineCommentForm>({
    resolver: zodResolver(timelineCommentFormSchema),
    defaultValues: {
      comment: "",
    },
  })
  const { control, handleSubmit } = methods

  if (caseEventsIsLoading) {
    return <CenteredSpinner />
  }

  if (caseEventsError || !caseEvents) {
    return (
      <AlertNotification level="error" message="Failed to load case events" />
    )
  }
  const onSubmit = async (data: TimelineCommentForm) => {
    console.log("data", data)
    await mutateCaseEventsAsync({
      type: "comment_created",
      data,
    })
  }

  return (
    <div className={className}>
      <div className="pl-4">
        {caseEvents.length > 0 ? (
          <ol className="relative mb-2 space-y-8 border-s border-gray-200 pb-8 pl-4 dark:border-gray-700">
            {caseEvents.map((caseEvent, index) => (
              <TimelineItem key={index} {...caseEvent} user={user} />
            ))}
          </ol>
        ) : (
          <NoContent className="min-h-10" message="No activity." />
        )}
      </div>
      <Form {...methods}>
        <form onSubmit={handleSubmit(onSubmit)} className="mb-10 h-full">
          <div className="relative overflow-hidden rounded-lg border bg-background shadow-sm focus-within:ring-1 focus-within:ring-ring">
            <FormField
              control={control}
              name="comment"
              render={({ field }) => (
                <FormItem>
                  <FormControl>
                    <Textarea
                      {...field}
                      id="comment"
                      placeholder="Add a comment..."
                      className="min-h-12 resize-none border-0 p-3 shadow-none focus-visible:ring-0"
                    />
                  </FormControl>
                  <FormMessage className="p-3" />
                </FormItem>
              )}
            />
            <div className="flex items-center p-3 pt-0">
              <Button type="submit" size="sm" className="ml-auto gap-1.5">
                Add Comment
                <ChatBubbleIcon className="size-3.5" />
              </Button>
            </div>
          </div>
        </form>
      </Form>
    </div>
  )
}
export type CaseEventType =
  | "status_changed"
  | "priority_changed"
  | "comment_created"
  | "case_opened"
  | "case_closed"

type TUser = ReturnType<typeof useUser>["user"]

export type TimelineItemProps = CaseEvent & {
  className?: string
  user?: TUser
}

export function TimelineItem({
  className,
  user,
  ...activityProps
}: TimelineItemProps) {
  return (
    <li className={cn("ms-6 rounded-lg text-xs shadow-sm", className)}>
      <UserAvatar
        className="absolute -start-4 flex size-8 items-center justify-center border ring-8 ring-white"
        src={user?.imageUrl}
        alt={userDefaults.alt}
      />
      <TimelineItemActivity {...activityProps} user={user} />
    </li>
  )
}
function TimelineItemActivity(props: TimelineItemProps) {
  const comment = props.data?.comment
  return (
    <div className="space-y-2 rounded-lg border border-gray-200 p-4 shadow-sm">
      <TimelineItemActivityHeader {...props} />
      {comment && (
        <TimelineItemActivityDetail>{comment}</TimelineItemActivityDetail>
      )}
    </div>
  )
}

const getActivityDescription = ({
  user,
  type,
  data,
}: TimelineItemProps): React.ReactNode => {
  const name = <b>{user?.fullName || userDefaults.name}</b>
  switch (type) {
    case "comment_created":
      return <span>{name} added a comment to the case.</span>
    case "case_opened":
      return <span>{name} opened the case.</span>
    case "case_closed":
      return <span>{name} closed the case.</span>
    case "status_changed":
      const statusVal = data?.status as CaseStatusType | null
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
    case "priority_changed":
      const priorityVal = data?.priority as CasePriorityType | null
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

function TimelineItemActivityHeader(props: TimelineItemProps) {
  const activityDescription = getActivityDescription(props)
  const { created_at, className } = props
  return (
    <div className={cn("items-center justify-between sm:flex", className)}>
      <time className="mb-1 text-xs font-normal text-muted-foreground sm:order-last sm:mb-0">
        {created_at.toLocaleString()}
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
