"use client"

import { useState } from "react"
import { formatDistanceToNow } from "date-fns"
import {
  AlertCircle,
  Calendar,
  ChevronDown,
  ChevronUp,
  Clock,
  FileText,
  MessageSquare,
  Paperclip,
  Tag,
  Users,
} from "lucide-react"

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Separator } from "@/components/ui/separator"

// Define the tagged union types for timeline events
type BaseTimelineEvent = {
  id: string
  timestamp: Date
  user: {
    id: string
    name: string
    avatar?: string
  }
}

type StatusChangeEvent = BaseTimelineEvent & {
  type: "status_change"
  from: string
  to: string
}

type CommentEvent = BaseTimelineEvent & {
  type: "comment"
  content: string
}

type AssignmentEvent = BaseTimelineEvent & {
  type: "assignment"
  assignees: {
    id: string
    name: string
    avatar?: string
  }[]
}

type PriorityChangeEvent = BaseTimelineEvent & {
  type: "priority_change"
  from: "low" | "medium" | "high" | "critical"
  to: "low" | "medium" | "high" | "critical"
}

type AttachmentEvent = BaseTimelineEvent & {
  type: "attachment"
  files: {
    id: string
    name: string
    size: string
    type: string
  }[]
}

type TagEvent = BaseTimelineEvent & {
  type: "tag"
  tags: string[]
}

type DueDateEvent = BaseTimelineEvent & {
  type: "due_date"
  date: Date
}

// Union type for all possible timeline events
type TimelineEvent =
  | StatusChangeEvent
  | CommentEvent
  | AssignmentEvent
  | PriorityChangeEvent
  | AttachmentEvent
  | TagEvent
  | DueDateEvent

// Mock data for the timeline
const timelineEvents: TimelineEvent[] = [
  {
    id: "1",
    type: "status_change",
    timestamp: new Date(2023, 4, 15, 9, 30),
    user: {
      id: "user1",
      name: "Alex Johnson",
      avatar: "/abstract-letter-aj.png",
    },
    from: "Open",
    to: "In Progress",
  },
  {
    id: "2",
    type: "comment",
    timestamp: new Date(2023, 4, 15, 10, 15),
    user: {
      id: "user2",
      name: "Sam Taylor",
      avatar: "/stylized-letter-st.png",
    },
    content:
      "I've started investigating this issue. Initial findings suggest it might be related to the recent API changes.",
  },
  {
    id: "3",
    type: "assignment",
    timestamp: new Date(2023, 4, 15, 11, 0),
    user: {
      id: "user1",
      name: "Alex Johnson",
      avatar: "/abstract-letter-aj.png",
    },
    assignees: [
      {
        id: "user3",
        name: "Jamie Smith",
        avatar: "/javascript-code.png",
      },
      {
        id: "user4",
        name: "Morgan Lee",
        avatar: "/machine-learning-concept.png",
      },
    ],
  },
  {
    id: "4",
    type: "priority_change",
    timestamp: new Date(2023, 4, 15, 13, 45),
    user: {
      id: "user3",
      name: "Jamie Smith",
      avatar: "/javascript-code.png",
    },
    from: "medium",
    to: "high",
  },
  {
    id: "5",
    type: "attachment",
    timestamp: new Date(2023, 4, 15, 14, 30),
    user: {
      id: "user2",
      name: "Sam Taylor",
      avatar: "/stylized-letter-st.png",
    },
    files: [
      {
        id: "file1",
        name: "error_logs.txt",
        size: "1.2 MB",
        type: "text/plain",
      },
      {
        id: "file2",
        name: "screenshot.png",
        size: "450 KB",
        type: "image/png",
      },
    ],
  },
  {
    id: "6",
    type: "tag",
    timestamp: new Date(2023, 4, 15, 15, 10),
    user: {
      id: "user1",
      name: "Alex Johnson",
      avatar: "/abstract-letter-aj.png",
    },
    tags: ["bug", "backend", "api"],
  },
  {
    id: "7",
    type: "due_date",
    timestamp: new Date(2023, 4, 15, 16, 0),
    user: {
      id: "user3",
      name: "Jamie Smith",
      avatar: "/javascript-code.png",
    },
    date: new Date(2023, 4, 17, 18, 0),
  },
  {
    id: "8",
    type: "comment",
    timestamp: new Date(2023, 4, 15, 16, 45),
    user: {
      id: "user4",
      name: "Morgan Lee",
      avatar: "/machine-learning-concept.png",
    },
    content:
      "After reviewing the logs, I found that the issue is related to the authentication service. We need to update the token validation logic.",
  },
  {
    id: "9",
    type: "status_change",
    timestamp: new Date(2023, 4, 16, 9, 0),
    user: {
      id: "user4",
      name: "Morgan Lee",
      avatar: "/machine-learning-concept.png",
    },
    from: "In Progress",
    to: "Under Review",
  },
]

// Helper function to get icon for event type
function getEventIcon(type: TimelineEvent["type"]) {
  switch (type) {
    case "status_change":
      return <AlertCircle className="size-4" />
    case "comment":
      return <MessageSquare className="size-4" />
    case "assignment":
      return <Users className="size-4" />
    case "priority_change":
      return <AlertCircle className="size-4" />
    case "attachment":
      return <Paperclip className="size-4" />
    case "tag":
      return <Tag className="size-4" />
    case "due_date":
      return <Calendar className="size-4" />
    default:
      return <FileText className="size-4" />
  }
}

// Helper function to get color for event type
function getEventColor(type: TimelineEvent["type"]) {
  switch (type) {
    case "status_change":
      return "bg-blue-500"
    case "comment":
      return "bg-gray-500"
    case "assignment":
      return "bg-purple-500"
    case "priority_change":
      return "bg-orange-500"
    case "attachment":
      return "bg-green-500"
    case "tag":
      return "bg-indigo-500"
    case "due_date":
      return "bg-red-500"
    default:
      return "bg-gray-500"
  }
}

// Helper function to get priority badge color
function getPriorityColor(priority: "low" | "medium" | "high" | "critical") {
  switch (priority) {
    case "low":
      return "bg-gray-200 text-gray-800"
    case "medium":
      return "bg-blue-200 text-blue-800"
    case "high":
      return "bg-orange-200 text-orange-800"
    case "critical":
      return "bg-red-200 text-red-800"
    default:
      return "bg-gray-200 text-gray-800"
  }
}

// Component to render a single timeline event
function TimelineEventItem({ event }: { event: TimelineEvent }) {
  const [expanded, setExpanded] = useState(false)

  return (
    <div className="relative pb-8 pl-6">
      {/* Timeline connector line */}
      <div className="absolute inset-y-0 left-0 w-px bg-border"></div>

      {/* Event dot */}
      <div
        className={`absolute left-[-4px] top-1.5 size-2 rounded-full ${getEventColor(event.type)}`}
      ></div>

      <div className="flex flex-col space-y-2">
        {/* Event header */}
        <div className="flex items-start justify-between">
          <div className="flex items-center space-x-2">
            <Avatar className="size-6">
              <AvatarImage
                src={event.user.avatar || "/placeholder.svg"}
                alt={event.user.name}
              />
              <AvatarFallback>
                {event.user.name
                  .split(" ")
                  .map((n) => n[0])
                  .join("")}
              </AvatarFallback>
            </Avatar>
            <span className="font-medium">{event.user.name}</span>
            <span className="text-sm text-muted-foreground">
              {formatDistanceToNow(event.timestamp, { addSuffix: true })}
            </span>
          </div>
          <div className="flex items-center space-x-1">
            <Badge
              variant="outline"
              className="flex items-center space-x-1 text-xs"
            >
              {getEventIcon(event.type)}
              <span>{event.type.replace("_", " ")}</span>
            </Badge>
            <Button
              variant="ghost"
              size="icon"
              className="size-6"
              onClick={() => setExpanded(!expanded)}
            >
              {expanded ? (
                <ChevronUp className="size-4" />
              ) : (
                <ChevronDown className="size-4" />
              )}
            </Button>
          </div>
        </div>

        {/* Event content based on type */}
        <div className={`pl-8 ${expanded ? "block" : "hidden"}`}>
          {event.type === "status_change" && (
            <div className="flex items-center space-x-2 text-sm">
              <span>Changed status from</span>
              <Badge variant="outline">{event.from}</Badge>
              <span>to</span>
              <Badge variant="outline">{event.to}</Badge>
            </div>
          )}

          {event.type === "comment" && (
            <Card className="p-3 text-sm">
              <p>{event.content}</p>
            </Card>
          )}

          {event.type === "assignment" && (
            <div className="flex flex-col space-y-2 text-sm">
              <span>Assigned to:</span>
              <div className="flex flex-wrap gap-2">
                {event.assignees.map((assignee) => (
                  <div
                    key={assignee.id}
                    className="flex items-center space-x-1 rounded-full bg-muted px-2 py-1"
                  >
                    <Avatar className="size-4">
                      <AvatarImage
                        src={assignee.avatar || "/placeholder.svg"}
                        alt={assignee.name}
                      />
                      <AvatarFallback>
                        {assignee.name
                          .split(" ")
                          .map((n) => n[0])
                          .join("")}
                      </AvatarFallback>
                    </Avatar>
                    <span>{assignee.name}</span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {event.type === "priority_change" && (
            <div className="flex items-center space-x-2 text-sm">
              <span>Changed priority from</span>
              <Badge className={getPriorityColor(event.from)}>
                {event.from}
              </Badge>
              <span>to</span>
              <Badge className={getPriorityColor(event.to)}>{event.to}</Badge>
            </div>
          )}

          {event.type === "attachment" && (
            <div className="flex flex-col space-y-2 text-sm">
              <span>Added {event.files.length} file(s):</span>
              <div className="flex flex-col space-y-1">
                {event.files.map((file) => (
                  <div
                    key={file.id}
                    className="flex items-center space-x-2 rounded bg-muted p-2"
                  >
                    <Paperclip className="size-4" />
                    <span>{file.name}</span>
                    <span className="text-xs text-muted-foreground">
                      {file.size}
                    </span>
                  </div>
                ))}
              </div>
            </div>
          )}

          {event.type === "tag" && (
            <div className="flex flex-col space-y-2 text-sm">
              <span>Added tags:</span>
              <div className="flex flex-wrap gap-1">
                {event.tags.map((tag) => (
                  <Badge key={tag} variant="secondary">
                    {tag}
                  </Badge>
                ))}
              </div>
            </div>
          )}

          {event.type === "due_date" && (
            <div className="flex items-center space-x-2 text-sm">
              <Clock className="size-4" />
              <span>
                Set due date to {event.date.toLocaleDateString()} at{" "}
                {event.date.toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            </div>
          )}
        </div>

        {/* Always visible summary for non-expanded view */}
        <div
          className={`pl-8 text-sm text-muted-foreground ${expanded ? "hidden" : "block"}`}
        >
          {event.type === "status_change" && (
            <div className="flex items-center space-x-1">
              <span>Changed status from</span>
              <span className="font-medium">{event.from}</span>
              <span>to</span>
              <span className="font-medium">{event.to}</span>
            </div>
          )}

          {event.type === "comment" && (
            <div className="line-clamp-1">{event.content}</div>
          )}

          {event.type === "assignment" && (
            <div className="flex items-center space-x-1">
              <span>Assigned to</span>
              <span className="font-medium">
                {event.assignees.map((a) => a.name).join(", ")}
              </span>
            </div>
          )}

          {event.type === "priority_change" && (
            <div className="flex items-center space-x-1">
              <span>Changed priority from</span>
              <span className="font-medium">{event.from}</span>
              <span>to</span>
              <span className="font-medium">{event.to}</span>
            </div>
          )}

          {event.type === "attachment" && (
            <div>Added {event.files.length} file(s)</div>
          )}

          {event.type === "tag" && (
            <div className="flex items-center space-x-1">
              <span>Added tags:</span>
              <span className="font-medium">{event.tags.join(", ")}</span>
            </div>
          )}

          {event.type === "due_date" && (
            <div>Set due date to {event.date.toLocaleDateString()}</div>
          )}
        </div>
      </div>
    </div>
  )
}

// Group events by date for better organization
function groupEventsByDate(events: TimelineEvent[]) {
  const grouped: Record<string, TimelineEvent[]> = {}

  events.forEach((event) => {
    const date = event.timestamp.toDateString()
    if (!grouped[date]) {
      grouped[date] = []
    }
    grouped[date].push(event)
  })

  // Sort dates in descending order (newest first)
  return Object.entries(grouped)
    .sort(
      ([dateA], [dateB]) =>
        new Date(dateB).getTime() - new Date(dateA).getTime()
    )
    .map(([date, events]) => ({
      date: new Date(date),
      events: events.sort(
        (a, b) => b.timestamp.getTime() - a.timestamp.getTime()
      ),
    }))
}

export function CaseTimeline() {
  const groupedEvents = groupEventsByDate(timelineEvents)

  return (
    <div className="mx-auto w-full max-w-3xl">
      <div className="mb-6 flex items-center justify-between">
        <h2 className="text-xl font-semibold">Case Timeline</h2>
        <Button variant="outline" size="sm" className="flex items-center gap-1">
          <Clock className="size-4" />
          <span>Filter</span>
        </Button>
      </div>

      <div className="space-y-6">
        {groupedEvents.map(({ date, events }) => (
          <div key={date.toISOString()} className="space-y-2">
            <div className="sticky top-0 z-10 bg-background py-2">
              <div className="flex items-center">
                <div className="font-medium">
                  {date.toLocaleDateString(undefined, {
                    weekday: "long",
                    month: "long",
                    day: "numeric",
                    year: "numeric",
                  })}
                </div>
                <Separator className="ml-4 flex-1" />
              </div>
            </div>

            <div className="space-y-0">
              {events.map((event) => (
                <TimelineEventItem key={event.id} event={event} />
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
