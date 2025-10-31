"use client"

import { useChat } from "@ai-sdk/react"
import { DefaultChatTransport } from "ai"
import {
  ChevronRightIcon,
  CircleDot,
  LoaderIcon,
  MessageCircle,
} from "lucide-react"
import type { ReactNode } from "react"
import { useMemo, useState } from "react"
import type { EventFailure, InteractionRead, Session_Any_ } from "@/client"
import {
  Conversation,
  ConversationContent,
  ConversationScrollButton,
} from "@/components/ai-elements/conversation"
import { getWorkflowEventIcon } from "@/components/builder/events/events-workflow"
import { MessagePart } from "@/components/chat/chat-session-pane"
import { CodeBlock } from "@/components/code-block"
import { JsonViewWithControls } from "@/components/json-viewer"
import { Spinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { InlineDotSeparator } from "@/components/separator"
import { Badge } from "@/components/ui/badge"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { toast } from "@/components/ui/use-toast"
import { parseChatError } from "@/hooks/use-chat"
import { isUIMessageArray } from "@/lib/agents"
import { getBaseUrl } from "@/lib/api"
import {
  groupEventsByActionRef,
  parseStreamId,
  refToLabel,
  type WorkflowExecutionEventCompact,
  type WorkflowExecutionReadCompact,
} from "@/lib/event-history"
import { useWorkflowBuilder } from "@/providers/builder"

type TabType = "input" | "result" | "interaction"

export function ActionEventPane({
  execution,
  type,
}: {
  execution: WorkflowExecutionReadCompact
  type: TabType
}) {
  const { workflowId, selectedActionEventRef, setSelectedActionEventRef } =
    useWorkflowBuilder()

  if (!workflowId)
    return <AlertNotification level="error" message="No workflow in context" />

  let events = execution.events
  if (type === "interaction") {
    // Filter events to only include interaction events
    const interactionEvents = new Set(
      execution.interactions?.map((s) => s.action_ref) ?? []
    )
    events = events.filter((e) => interactionEvents.has(e.action_ref))
  }
  const groupedEvents = groupEventsByActionRef(events)
  return (
    <div className="flex flex-col gap-4 p-4">
      <Select
        value={selectedActionEventRef}
        onValueChange={setSelectedActionEventRef}
      >
        <SelectTrigger className="h-8 text-xs text-foreground/70 focus:ring-0 focus:ring-offset-0">
          <SelectValue placeholder="Select an event" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {Object.entries(groupedEvents).map(([actionRef, relatedEvents]) => (
              <SelectItem
                key={actionRef}
                value={actionRef}
                className="max-h-8 py-1 text-xs"
              >
                {refToLabel(actionRef)}
                {relatedEvents.length !== 1 && ` (${relatedEvents.length})`}
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>

      <ActionEventView
        selectedRef={selectedActionEventRef}
        execution={execution}
        type={type}
      />
    </div>
  )
}

function ActionEventView({
  selectedRef,
  execution,
  type,
}: {
  selectedRef?: string
  execution: WorkflowExecutionReadCompact
  type: TabType
}) {
  const noEvent = (
    <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
      <CircleDot className="size-3 text-muted-foreground" />
      <span>Please select an event</span>
    </div>
  )
  if (!selectedRef) {
    return noEvent
  }
  if (type === "interaction") {
    const interaction = execution.interactions?.find(
      (s) => s.action_ref === selectedRef
    )
    if (!interaction) {
      // We reach this if we switch tabs or select an event that has no interaction state
      return noEvent
    }
    return (
      <ActionInteractionEventDetails
        eventRef={selectedRef}
        interaction={interaction}
      />
    )
  }
  return (
    <ActionEventDetails
      eventRef={selectedRef}
      status={execution.status}
      events={execution.events}
      type={type}
    />
  )
}

function ActionInteractionEventDetails({
  eventRef,
  interaction,
}: {
  eventRef: string
  interaction: InteractionRead
}) {
  if (interaction.response_payload === null) {
    return (
      <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
        <CircleDot className="size-3 text-muted-foreground" />
        <span>No interaction data</span>
      </div>
    )
  }
  return (
    <div className="flex flex-col gap-4">
      <JsonViewWithControls
        src={interaction.response_payload}
        defaultExpanded={true}
        copyPrefix={`ACTIONS.${eventRef}.interaction`}
      />
    </div>
  )
}

export function SuccessEvent({
  event,
  type,
  eventRef,
  defaultExpanded = true,
  defaultTab = "nested",
}: {
  event: WorkflowExecutionEventCompact
  type: Omit<TabType, "interaction">
  eventRef: string
  defaultExpanded?: boolean
  defaultTab?: "nested" | "flat"
}) {
  switch (type) {
    case "input":
      return (
        <JsonViewWithControls
          src={event.action_input}
          defaultExpanded={defaultExpanded}
          defaultTab={defaultTab}
        />
      )
    case "result":
      return (
        <JsonViewWithControls
          src={event.action_result}
          defaultExpanded={defaultExpanded}
          defaultTab={defaultTab}
          copyPrefix={`ACTIONS.${eventRef}.result`}
        />
      )
  }
  return null
}

function StreamDetails({
  streamId,
  placeholder,
}: {
  streamId?: string
  placeholder?: string
}) {
  if (!streamId) {
    return (
      <div className="flex items-center gap-1 text-xs text-muted-foreground/80">
        <span>{placeholder}</span>
      </div>
    )
  }
  return (
    <div className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground/80">
      {parseStreamId(streamId)
        .filter((part) => part.scope !== "<root>")
        // Only sort if scope matches, otherwise preserve original order
        .sort((a, b) => {
          if (a.scope === b.scope) {
            return Number(a.index) - Number(b.index)
          }
          // If scopes do not match, preserve original order (no sorting)
          return 0
        })
        // Insert a ">" separator between mapped elements, but not after the last one
        .map((part, idx, arr) => (
          <div key={part.scope} className="flex items-center gap-1">
            <span className="flex items-center gap-1">
              <span>{part.scope}</span>
              <InlineDotSeparator />
              <span>{part.index}</span>
            </span>
            {idx < arr.length - 1 && <ChevronRightIcon className="size-3" />}
          </div>
        ))}
    </div>
  )
}

function ActionEventContent({
  actionEvent,
  type,
  eventRef,
  streamIdPlaceholder,
}: {
  actionEvent: WorkflowExecutionEventCompact
  type: Omit<TabType, "interaction">
  eventRef: string
  streamIdPlaceholder?: string
}) {
  const { status, session, stream_id, action_error } = actionEvent
  switch (status) {
    case "SCHEDULED": {
      return (
        <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
          <LoaderIcon className="size-3 animate-spin text-muted-foreground" />
          <span>Action is scheduled...</span>
        </div>
      )
    }
    case "STARTED": {
      // If a session exists, always use ActionSessionStream
      // Works for both live streaming and completed states
      // (backend converts completed AgentOutput to UIMessages automatically)
      if (session) {
        return <ActionSessionStream session={session} />
      }
      return (
        <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
          <LoaderIcon className="size-3 animate-spin text-muted-foreground" />
          <span>Action is running...</span>
        </div>
      )
    }
    default: {
      const showSessionTabs =
        type === "result" && !!session && !action_error && !streamIdPlaceholder

      if (showSessionTabs && session) {
        return (
          <Tabs defaultValue="session" className="flex flex-col gap-2">
            <div className="flex flex-wrap items-center gap-2">
              <Badge variant="secondary" className="items-center gap-2">
                {getWorkflowEventIcon(actionEvent.status, "size-4")}
                <span className="text-xs font-semibold text-foreground/70">
                  Action {status.toLowerCase()}
                </span>
              </Badge>
              <div className="ml-auto flex flex-wrap items-center gap-2">
                <StreamDetails
                  streamId={stream_id}
                  placeholder={streamIdPlaceholder}
                />
                <TabsList className="h-8">
                  <TabsTrigger
                    value="session"
                    disableUnderline
                    className="h-6 text-xs"
                  >
                    Session
                  </TabsTrigger>
                  <TabsTrigger
                    value="result"
                    disableUnderline
                    className="h-6 text-xs"
                  >
                    Result
                  </TabsTrigger>
                </TabsList>
              </div>
            </div>
            <TabsContent value="session" className="mt-1">
              <ActionSessionStream session={session} />
            </TabsContent>
            <TabsContent value="result" className="mt-1">
              <JsonViewWithControls
                src={actionEvent.action_result}
                defaultExpanded={true}
                defaultTab="nested"
                copyPrefix={`ACTIONS.${eventRef}.result`}
              />
            </TabsContent>
          </Tabs>
        )
      }

      return (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <Badge variant="secondary" className="items-center gap-2">
              {getWorkflowEventIcon(actionEvent.status, "size-4")}
              <span className="text-xs font-semibold text-foreground/70">
                Action {status.toLowerCase()}
              </span>
            </Badge>
            <StreamDetails
              streamId={stream_id}
              placeholder={streamIdPlaceholder}
            />
          </div>

          {/* Present result or error */}
          {type === "result" && action_error ? (
            <ErrorEvent failure={action_error} />
          ) : (
            <SuccessEvent event={actionEvent} type={type} eventRef={eventRef} />
          )}
        </div>
      )
    }
  }
}

export function ActionEventDetails({
  eventRef,
  status,
  events,
  type,
}: {
  eventRef: string
  status: WorkflowExecutionReadCompact["status"]
  events: WorkflowExecutionEventCompact[]
  type: Omit<TabType, "interaction">
}) {
  const actionEventsForRef = events.filter((e) => e.action_ref === eventRef)
  // No events for ref, either the action has not executed or there was no event for the action.
  if (actionEventsForRef.length === 0) {
    return (
      <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
        {status === "RUNNING" ? (
          <>
            <LoaderIcon className="size-3 animate-spin text-muted-foreground" />
            <span>Waiting for events...</span>
          </>
        ) : (
          <>
            <CircleDot className="size-3 text-muted-foreground" />
            <span>No events</span>
          </>
        )}
      </div>
    )
  }
  if (type === "input") {
    // Inputs are identical for all events, so we can just render the first one
    return (
      <ActionEventContent
        actionEvent={actionEventsForRef[0]}
        type={type}
        eventRef={eventRef}
        streamIdPlaceholder="Input is the same for all events"
      />
    )
  }
  return actionEventsForRef.map((actionEvent) => (
    <div key={actionEvent.stream_id}>
      <ActionEventContent
        actionEvent={actionEvent}
        type={type}
        eventRef={eventRef}
      />
    </div>
  ))
}

function ActionSessionStream({ session }: { session: Session_Any_ }) {
  const messages = isUIMessageArray(session.events) ? session.events : undefined

  if (messages && messages.length > 0) {
    return (
      <ActionSessionShell>
        <Conversation className="flex-1">
          <ConversationContent>
            {messages.map(({ id, role, parts }) => (
              <div key={id}>
                {parts?.map((part, partIdx) => (
                  <MessagePart
                    part={part}
                    partIdx={partIdx}
                    id={id}
                    role={role}
                    isLastMessage={id === messages[messages.length - 1].id}
                  />
                ))}
              </div>
            ))}
          </ConversationContent>
          <ConversationScrollButton />
        </Conversation>
      </ActionSessionShell>
    )
  }

  return <ActionSessionLiveStream sessionId={session.id} />
}

function ActionSessionLiveStream({ sessionId }: { sessionId: string }) {
  const { workspaceId } = useWorkflowBuilder()
  // TODO: Surface error in UI
  const [_lastError, setLastError] = useState<string | null>(null)

  const transport = useMemo(() => {
    return new DefaultChatTransport({
      credentials: "include",
      prepareReconnectToStreamRequest: ({ id }) => {
        const url = new URL(`/api/agent/sessions/${id}`, getBaseUrl())
        url.searchParams.set("workspace_id", workspaceId)
        return {
          api: url.toString(),
          credentials: "include", // Include cookies/auth
        }
      },
    })
  }, [workspaceId])

  const { messages, status } = useChat({
    id: sessionId,
    resume: true, // Force resume a stream on mount
    transport,
    onError: (error) => {
      const friendlyMessage = parseChatError(error)
      setLastError(friendlyMessage)
      console.error("Error in Vercel chat:", error)
      toast({
        title: "Chat error",
        description: friendlyMessage,
      })
    },
  })

  const headerStatus = status === "streaming" ? "streaming" : undefined

  return (
    <ActionSessionShell status={headerStatus}>
      {status === "submitted" ? (
        <div className="flex flex-1 items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
          <Spinner className="size-3" />
          <span>Connecting to agent…</span>
        </div>
      ) : (
        <Conversation className="flex-1">
          <ConversationContent>
            {messages.map(({ id, role, parts }) => (
              <div key={id}>
                {parts?.map((part, partIdx) => (
                  <MessagePart
                    key={`${id}-${partIdx}`}
                    part={part}
                    partIdx={partIdx}
                    id={id}
                    role={role}
                    status={status}
                    isLastMessage={id === messages[messages.length - 1].id}
                  />
                ))}
              </div>
            ))}
          </ConversationContent>
          <ConversationScrollButton />
        </Conversation>
      )}
    </ActionSessionShell>
  )
}

function ActionSessionShell({
  status,
  children,
}: {
  status?: string
  children: ReactNode
}) {
  return (
    <div className="flex h-full flex-col overflow-hidden rounded-md border bg-card">
      <div className="flex items-center gap-2 border-b bg-muted/40 px-3 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
        <MessageCircle className="size-3" />
        <span>Session</span>
        {status === "streaming" && (
          <span className="ml-auto flex items-center gap-1 text-[10px] font-medium normal-case text-muted-foreground">
            <Spinner className="size-3" />
            <span>Streaming…</span>
          </span>
        )}
      </div>
      <div className="mb-8 flex min-h-[160px] flex-1 flex-col">{children}</div>
    </div>
  )
}

function ErrorEvent({ failure }: { failure: EventFailure }) {
  return (
    <div className="flex flex-col space-y-8 text-xs">
      <CodeBlock title="Error Message">{failure.message}</CodeBlock>
    </div>
  )
}
