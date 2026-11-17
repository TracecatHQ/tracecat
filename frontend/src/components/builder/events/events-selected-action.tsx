"use client"

import { CircleDot } from "lucide-react"
import { ActionEventDetails } from "@/components/executions/action-event-details"
import { AlertNotification } from "@/components/notifications"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  groupEventsByActionRef,
  refToLabel,
  type WorkflowExecutionEventCompact,
  type WorkflowExecutionReadCompact,
} from "@/lib/event-history"
import { useWorkflowBuilder } from "@/providers/builder"

type TabType = "input" | "result"

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

  const groupedEvents = groupEventsByActionRef(execution.events)
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
            {(
              Object.entries(groupedEvents) as [
                string,
                WorkflowExecutionEventCompact[],
              ][]
            ).map(([actionRef, relatedEvents]) => (
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
  return (
    <ActionEventDetails
      executionId={execution.id}
      actionRef={selectedRef}
      status={execution.status}
      events={execution.events}
      type={type}
    />
  )
}
