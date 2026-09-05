"use client"

import { CircleDot, PinIcon } from "lucide-react"
import { useState } from "react"
import type { InteractionRead } from "@/client"
import { ActionEventDetails } from "@/components/executions/action-event-details"
import { JsonViewWithControls } from "@/components/json-viewer"
import { AlertNotification } from "@/components/notifications"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import {
  getSyntheticPinnedEventMeta,
  groupEventsByActionRef,
  refToLabel,
  type WorkflowExecutionEventCompact,
  type WorkflowExecutionReadCompact,
} from "@/lib/event-history"
import {
  getWorkflowDraftPins,
  isPinnableActionEvent,
  type WorkflowDraftPins,
} from "@/lib/workflow-pins"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"

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
  const { workflow, updateWorkflow } = useWorkflow()
  const [isSavingPins, setIsSavingPins] = useState(false)
  const draftPins = getWorkflowDraftPins(workflow)
  const isResultTab = type === "result"

  if (!workflowId)
    return <AlertNotification level="error" message="No workflow in context" />

  let events = execution.events
  if (type === "interaction") {
    // Filter events to only include interaction events
    const interactionEvents = new Set(
      execution.interactions?.map((s: InteractionRead) => s.action_ref) ?? []
    )
    events = events.filter((e: WorkflowExecutionEventCompact) =>
      interactionEvents.has(e.action_ref)
    )
  }
  const groupedEvents = groupEventsByActionRef(events)
  const selectedEvents = selectedActionEventRef
    ? groupedEvents[selectedActionEventRef]
    : undefined
  const selectedRefMatchesPinSource =
    draftPins !== null &&
    (draftPins.source_execution_id === execution.id ||
      selectedEvents?.some(
        (event) => getSyntheticPinnedEventMeta(event) !== null
      ) === true)
  const selectedRefIsPinned =
    isResultTab &&
    selectedActionEventRef !== undefined &&
    selectedRefMatchesPinSource &&
    draftPins?.action_refs.includes(selectedActionEventRef)
  const canPinSelected = isPinnableActionEvent(
    selectedActionEventRef,
    groupedEvents,
    workflow?.actions
  )

  const saveDraftPins = async (
    nextPins: WorkflowDraftPins | null
  ): Promise<boolean> => {
    setIsSavingPins(true)
    try {
      await updateWorkflow({ draft_pins: nextPins })
      return true
    } catch {
      return false
    } finally {
      setIsSavingPins(false)
    }
  }

  const handlePinSelected = async () => {
    if (!selectedActionEventRef) {
      return
    }
    const nextRefs =
      draftPins?.source_execution_id === execution.id
        ? Array.from(
            new Set([...draftPins.action_refs, selectedActionEventRef])
          )
        : [selectedActionEventRef]
    const saved = await saveDraftPins({
      source_execution_id: execution.id,
      action_refs: nextRefs,
    })
    if (!saved) {
      return
    }
    toast({
      title: "Pinned action result",
      description: `ACTIONS.${selectedActionEventRef}.result is now pinned for draft runs.`,
    })
  }

  const handleUnpinSelected = async () => {
    if (!selectedActionEventRef || !selectedRefIsPinned || !draftPins) {
      return
    }
    const nextRefs = draftPins.action_refs.filter(
      (ref) => ref !== selectedActionEventRef
    )
    const saved = await saveDraftPins(
      nextRefs.length > 0
        ? {
            source_execution_id: draftPins.source_execution_id,
            action_refs: nextRefs,
          }
        : null
    )
    if (!saved) {
      return
    }
    toast({
      title: "Unpinned action result",
      description: `ACTIONS.${selectedActionEventRef}.result will be computed again.`,
    })
  }

  const handleClearPins = async () => {
    const saved = await saveDraftPins(null)
    if (!saved) {
      return
    }
    toast({
      title: "Cleared draft pins",
      description: "All pinned draft action results were removed.",
    })
  }

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
      {isResultTab && (
        <div className="flex flex-wrap items-center gap-2">
          <Badge variant="secondary" className="font-normal">
            <PinIcon className="mr-1 size-3" />
            {draftPins?.action_refs.length ?? 0} pinned
          </Badge>
          {draftPins && (
            <Badge
              variant="outline"
              className="font-mono text-[10px] font-normal"
            >
              Source: {draftPins.source_execution_id}
            </Badge>
          )}
          <Button
            type="button"
            size="sm"
            variant={selectedRefIsPinned ? "outline" : "secondary"}
            disabled={
              selectedRefIsPinned
                ? !selectedActionEventRef || isSavingPins
                : !canPinSelected || isSavingPins
            }
            onClick={
              selectedRefIsPinned ? handleUnpinSelected : handlePinSelected
            }
            className="h-7 text-xs"
          >
            {selectedRefIsPinned ? "Unpin selected" : "Pin selected"}
          </Button>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={!draftPins || isSavingPins}
            onClick={handleClearPins}
            className="h-7 text-xs"
          >
            Clear pins
          </Button>
        </div>
      )}

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
      (s: InteractionRead) => s.action_ref === selectedRef
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
      executionId={execution.id}
      actionRef={selectedRef}
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
        copyMode="jsonpath-and-payload"
      />
    </div>
  )
}
