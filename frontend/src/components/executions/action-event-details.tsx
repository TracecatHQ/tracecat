"use client"

import {
  ChevronLeftIcon,
  ChevronRightIcon,
  CircleDot,
  LoaderIcon,
} from "lucide-react"
import dynamic from "next/dynamic"
import type { ReactNode } from "react"
import { useEffect, useMemo, useState } from "react"
import type { EventFailure } from "@/client"
import { CodeBlock } from "@/components/code-block"
import { getWorkflowEventIcon } from "@/components/events/workflow-event-status"
import { CollectionObjectResult } from "@/components/executions/collection-object-result"
import { ExternalObjectResult } from "@/components/executions/external-object-result"
import { JsonViewWithControls } from "@/components/json-viewer"
import { InlineDotSeparator } from "@/components/separator"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Carousel,
  type CarouselApi,
  CarouselContent,
  CarouselItem,
} from "@/components/ui/carousel"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  getCompactEventTimestamp,
  parseStreamId,
  WF_TRIGGER_EVENT_REF,
  type WorkflowExecutionEventCompact,
  type WorkflowExecutionReadCompact,
} from "@/lib/event-history"
import {
  isCollectionStoredObject,
  isExternalStoredObject,
} from "@/lib/stored-object"

const ActionSessionStream = dynamic(
  () =>
    import("@/components/executions/action-session-stream").then(
      (module) => module.ActionSessionStream
    ),
  {
    ssr: false,
    loading: () => (
      <div className="flex min-h-40 items-center justify-center gap-2 text-xs text-muted-foreground">
        <LoaderIcon className="size-3 animate-spin" />
        <span>Loading session...</span>
      </div>
    ),
  }
)

/** Payload kind shown for an action event. */
export type ActionEventPayloadType = "input" | "result"

/** Multi-event presentation used by an action details surface. */
export type ActionEventPresentation = "stack" | "single"

/** Props for the shared workflow action event renderer. */
export interface ActionEventDetailsProps {
  executionId: string
  actionRef: string
  status: WorkflowExecutionReadCompact["status"]
  events: WorkflowExecutionEventCompact[]
  type: ActionEventPayloadType
  presentation?: ActionEventPresentation
}

/** Render the input or results for every compact event matching an action ref. */
export function ActionEventDetails({
  executionId,
  actionRef,
  status,
  events,
  type,
  presentation = "stack",
}: ActionEventDetailsProps) {
  const actionEventsForRef = events.filter(
    (event) => event.action_ref === actionRef
  )

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
    const firstInput = JSON.stringify(
      actionEventsForRef[0].action_input ?? null
    )
    const hasDistinctInputs = actionEventsForRef.some(
      (event) => JSON.stringify(event.action_input ?? null) !== firstInput
    )

    if (hasDistinctInputs) {
      return (
        <SingleActionEventPayload
          events={actionEventsForRef}
          executionId={executionId}
          eventRef={actionRef}
          type="input"
        />
      )
    }

    return (
      <ActionEventContent
        actionEvent={actionEventsForRef[0]}
        executionId={executionId}
        type={type}
        eventRef={actionRef}
        streamIdPlaceholder="Input is the same for all events"
      />
    )
  }

  if (presentation === "single") {
    return (
      <SingleActionEventPayload
        events={actionEventsForRef}
        executionId={executionId}
        eventRef={actionRef}
      />
    )
  }

  return (
    <StackedActionEventResults
      events={actionEventsForRef}
      executionId={executionId}
      eventRef={actionRef}
    />
  )
}

function SingleActionEventPayload({
  events,
  executionId,
  eventRef,
  type = "result",
}: {
  events: WorkflowExecutionEventCompact[]
  executionId: string
  eventRef: string
  type?: ActionEventPayloadType
}) {
  const sortedEvents = useMemo(
    () => [...events].sort(compareActionEvents),
    [events]
  )
  const [selectedEventId, setSelectedEventId] = useState<number | null>(null)
  const pinnedIndex =
    selectedEventId === null
      ? -1
      : sortedEvents.findIndex(
          (event) => event.source_event_id === selectedEventId
        )
  const currentIndex = pinnedIndex >= 0 ? pinnedIndex : sortedEvents.length - 1
  const actionEvent = sortedEvents[currentIndex]
  const canSelectPrevious = currentIndex > 0
  const canSelectNext = currentIndex < sortedEvents.length - 1

  function selectEvent(index: number) {
    const event = sortedEvents[index]
    if (event) {
      setSelectedEventId(event.source_event_id)
    }
  }

  return (
    <div className="flex flex-col gap-3 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <StreamDetails streamId={actionEvent.stream_id} />
        {sortedEvents.length > 1 && (
          <div className="flex items-center gap-2">
            <StreamNav
              currentIndex={currentIndex}
              total={sortedEvents.length}
              canGoPrevious={canSelectPrevious}
              canGoNext={canSelectNext}
              onPrevious={() => selectEvent(currentIndex - 1)}
              onNext={() => selectEvent(currentIndex + 1)}
            />
          </div>
        )}
      </div>
      <ActionEventContent
        key={actionEvent.source_event_id}
        actionEvent={actionEvent}
        executionId={executionId}
        type={type}
        eventRef={eventRef}
        showStreamDetails={false}
      />
    </div>
  )
}

function StreamNav({
  currentIndex,
  total,
  canGoPrevious,
  canGoNext,
  onPrevious,
  onNext,
}: {
  currentIndex: number
  total: number
  canGoPrevious: boolean
  canGoNext: boolean
  onPrevious: () => void
  onNext: () => void
}) {
  return (
    <>
      <span className="text-xs text-muted-foreground">
        Stream {currentIndex + 1} of {total}
      </span>
      <Button
        variant="outline"
        size="icon"
        className="h-7 w-7"
        onClick={onPrevious}
        disabled={!canGoPrevious}
        aria-label="Previous stream"
      >
        <ChevronLeftIcon className="size-4" />
      </Button>
      <Button
        variant="outline"
        size="icon"
        className="h-7 w-7"
        onClick={onNext}
        disabled={!canGoNext}
        aria-label="Next stream"
      >
        <ChevronRightIcon className="size-4" />
      </Button>
    </>
  )
}

function StackedActionEventResults({
  events,
  executionId,
  eventRef,
}: {
  events: WorkflowExecutionEventCompact[]
  executionId: string
  eventRef: string
}) {
  const eventsWithSessions = events.filter((event) => event.session)

  if (eventsWithSessions.length > 1) {
    const firstSessionIndex = events.findIndex((event) => event.session)
    const nodes: ReactNode[] = []

    events.forEach((actionEvent, index) => {
      const key = actionEvent.stream_id ?? actionEvent.source_event_id
      if (actionEvent.session) {
        if (index === firstSessionIndex) {
          nodes.push(
            <div key="session-streams">
              <ActionSessionCarousel
                events={eventsWithSessions}
                executionId={executionId}
                eventRef={eventRef}
              />
            </div>
          )
        }
        return
      }

      nodes.push(
        <div key={key}>
          <ActionEventContent
            actionEvent={actionEvent}
            executionId={executionId}
            type="result"
            eventRef={eventRef}
          />
        </div>
      )
    })

    return nodes
  }

  return events.map((actionEvent) => (
    <div key={actionEvent.stream_id ?? actionEvent.source_event_id}>
      <ActionEventContent
        actionEvent={actionEvent}
        executionId={executionId}
        type="result"
        eventRef={eventRef}
      />
    </div>
  ))
}

function ActionSessionCarousel({
  events,
  executionId,
  eventRef,
}: {
  events: WorkflowExecutionEventCompact[]
  executionId: string
  eventRef: string
}) {
  const [api, setApi] = useState<CarouselApi>()
  const [currentIndex, setCurrentIndex] = useState(0)
  const [canScrollPrev, setCanScrollPrev] = useState(false)
  const [canScrollNext, setCanScrollNext] = useState(events.length > 1)

  useEffect(() => {
    if (!api) {
      return
    }

    const update = () => {
      setCurrentIndex(api.selectedScrollSnap())
      setCanScrollPrev(api.canScrollPrev())
      setCanScrollNext(api.canScrollNext())
    }

    update()
    api.on("select", update)
    api.on("reInit", update)

    return () => {
      api.off("select", update)
      api.off("reInit", update)
    }
  }, [api])

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-end gap-2">
        <StreamNav
          currentIndex={currentIndex}
          total={events.length}
          canGoPrevious={canScrollPrev}
          canGoNext={canScrollNext}
          onPrevious={() => api?.scrollPrev()}
          onNext={() => api?.scrollNext()}
        />
      </div>
      <Carousel
        setApi={setApi}
        opts={{
          align: "start",
          duration: 0,
          dragFree: false,
          containScroll: false,
          watchDrag: false,
          loop: true,
        }}
      >
        <CarouselContent>
          {events.map((actionEvent, index) => (
            <CarouselItem
              key={`session-${actionEvent.stream_id ?? actionEvent.source_event_id ?? index}`}
            >
              <ActionEventContent
                actionEvent={actionEvent}
                executionId={executionId}
                type="result"
                eventRef={eventRef}
              />
            </CarouselItem>
          ))}
        </CarouselContent>
      </Carousel>
    </div>
  )
}

function ActionEventContent({
  actionEvent,
  executionId,
  type,
  eventRef,
  streamIdPlaceholder,
  showStreamDetails = true,
}: {
  actionEvent: WorkflowExecutionEventCompact
  executionId: string
  type: ActionEventPayloadType
  eventRef: string
  streamIdPlaceholder?: string
  showStreamDetails?: boolean
}) {
  const { status, session, stream_id, action_error } = actionEvent

  if (type === "result") {
    switch (status) {
      case "SCHEDULED":
        return (
          <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
            <LoaderIcon className="size-3 animate-spin text-muted-foreground" />
            <span>Action is scheduled...</span>
          </div>
        )
      case "STARTED":
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
  }

  const showSessionTabs =
    type === "result" && !!session && !action_error && !streamIdPlaceholder

  if (showSessionTabs && session) {
    return (
      <Tabs defaultValue="session" className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <EventStatusBadge status={status} />
          <div className="ml-auto flex flex-wrap items-center gap-2">
            {showStreamDetails && (
              <StreamDetails
                streamId={stream_id}
                placeholder={streamIdPlaceholder}
              />
            )}
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
          <ActionResultViewer
            result={actionEvent.action_result}
            eventRef={eventRef}
            executionId={executionId}
            eventId={actionEvent.source_event_id}
            defaultExpanded={true}
          />
        </TabsContent>
      </Tabs>
    )
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <EventStatusBadge status={status} />
        {showStreamDetails && (
          <StreamDetails
            streamId={stream_id}
            placeholder={streamIdPlaceholder}
          />
        )}
      </div>
      {type === "result" && action_error ? (
        <ErrorEvent failure={action_error} />
      ) : (
        <SuccessEvent
          event={actionEvent}
          type={type}
          eventRef={eventRef}
          executionId={executionId}
          eventId={actionEvent.source_event_id}
        />
      )}
    </div>
  )
}

function EventStatusBadge({
  status,
}: {
  status: WorkflowExecutionEventCompact["status"]
}) {
  return (
    <Badge variant="secondary" className="items-center gap-2">
      {getWorkflowEventIcon(status, "size-4")}
      <span className="text-xs font-semibold text-foreground/70">
        Action {status.toLowerCase()}
      </span>
    </Badge>
  )
}

/** Render one successful action input or result with shared copy behavior. */
export function SuccessEvent({
  event,
  type,
  eventRef,
  executionId,
  eventId,
  defaultExpanded = true,
}: {
  event: WorkflowExecutionEventCompact
  type: ActionEventPayloadType
  eventRef: string
  executionId: string
  eventId: number
  defaultExpanded?: boolean
}) {
  if (type === "input" || eventRef === WF_TRIGGER_EVENT_REF) {
    return (
      <JsonViewWithControls
        src={event.action_input}
        defaultExpanded={defaultExpanded}
        copyPrefix={getEventCopyPrefix(eventRef, "input")}
        copyMode="jsonpath-and-payload"
      />
    )
  }

  return (
    <ActionResultViewer
      result={event.action_result}
      eventRef={eventRef}
      executionId={executionId}
      eventId={eventId}
      defaultExpanded={defaultExpanded}
    />
  )
}

function ActionResultViewer({
  result,
  eventRef,
  executionId,
  eventId,
  defaultExpanded = true,
}: {
  result: unknown
  eventRef: string
  executionId: string
  eventId: number
  defaultExpanded?: boolean
}) {
  if (result === null || result === undefined) {
    return (
      <div className="flex items-center justify-center gap-2 p-4 text-xs text-muted-foreground">
        <CircleDot className="size-3 text-muted-foreground" />
        <span>This action returned no result (null).</span>
      </div>
    )
  }

  if (isExternalStoredObject(result)) {
    return (
      <ExternalObjectResult
        executionId={executionId}
        eventId={eventId}
        external={result}
      />
    )
  }

  if (isCollectionStoredObject(result)) {
    return (
      <CollectionObjectResult
        executionId={executionId}
        eventId={eventId}
        collection={result}
        copyMode="jsonpath-and-payload"
        copyPrefix={getEventCopyPrefix(eventRef, "result")}
      />
    )
  }

  return (
    <JsonViewWithControls
      src={result}
      defaultExpanded={defaultExpanded}
      copyPrefix={getEventCopyPrefix(eventRef, "result")}
      copyMode="jsonpath-and-payload"
    />
  )
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

  const streamParts = parseStreamId(streamId)
    .filter((part) => part.scope !== "<root>")
    .sort((a, b) => {
      if (a.scope === b.scope) {
        return Number(a.index) - Number(b.index)
      }
      return 0
    })

  if (streamParts.length === 0) {
    return (
      <div className="flex items-center gap-1 text-xs text-muted-foreground/80">
        <span>Global scope</span>
      </div>
    )
  }

  return (
    <div className="flex flex-wrap items-center gap-1 text-xs text-muted-foreground/80">
      {streamParts.map((part, index) => (
        <div
          key={`${part.scope}:${part.index}:${index}`}
          className="flex items-center gap-1"
        >
          <span className="flex items-center gap-1">
            <span>{part.scope}</span>
            <InlineDotSeparator />
            <span>{part.index}</span>
          </span>
          {index < streamParts.length - 1 && (
            <ChevronRightIcon className="size-3" />
          )}
        </div>
      ))}
    </div>
  )
}

function ErrorEvent({ failure }: { failure: EventFailure }) {
  return (
    <div className="flex flex-col space-y-8 text-xs">
      <CodeBlock title="Error Message">
        {failure.root_cause_message ?? failure.message}
      </CodeBlock>
    </div>
  )
}

function getEventCopyPrefix(
  eventRef: string,
  kind: ActionEventPayloadType
): string {
  if (eventRef === WF_TRIGGER_EVENT_REF) {
    return "TRIGGER"
  }
  return kind === "input" ? `ACTIONS.${eventRef}` : `ACTIONS.${eventRef}.result`
}

function compareActionEvents(
  left: WorkflowExecutionEventCompact,
  right: WorkflowExecutionEventCompact
): number {
  const timestampDifference =
    getCompactEventTimestamp(left) - getCompactEventTimestamp(right)
  if (timestampDifference !== 0) {
    return timestampDifference
  }
  return left.source_event_id - right.source_event_id
}
