import React from "react"
import { DSLRunArgs, EventHistoryResponse, UDFActionInput } from "@/client"
import JsonView from "react18-json-view"

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"

import "react18-json-view/src/style.css"

import { InfoIcon, TriangleAlert } from "lucide-react"

import {
  ERROR_EVENT_TYPES,
  getRelativeTime,
  parseEventType,
} from "@/lib/event-history"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import { GenericWorkflowIcon, getIcon } from "@/components/icons"

/**
 * Event history for a specific workflow execution
 * @param param0
 * @returns
 */
export function WorkflowExecutionEventDetailView({
  event,
}: {
  event: EventHistoryResponse
}) {
  return (
    <div className="size-full overflow-auto">
      {/* Metadata */}
      <Accordion
        type="multiple"
        defaultValue={["general", "execution-context", "failure", "result"]}
      >
        {/* General */}
        <AccordionItem value="general">
          <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
            <div className="flex items-center">
              <InfoIcon
                className="mr-2 size-5 fill-sky-500 stroke-white"
                strokeWidth={2}
              />
              <span>Event Information</span>
            </div>
          </AccordionTrigger>
          <AccordionContent className="space-y-4">
            <EventGeneralInfo event={event} />
          </AccordionContent>
        </AccordionItem>

        {/* Urgent */}
        {event.failure && (
          <AccordionItem value="failure">
            <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
              <div className="flex items-end">
                <TriangleAlert
                  className="mr-2 size-5 fill-rose-500 stroke-white"
                  strokeWidth={2}
                />
                <span>Event Failure</span>
              </div>
            </AccordionTrigger>
            <AccordionContent>
              <div className="my-4 flex flex-col space-y-8 px-4">
                <div className="rounded-md border p-4 shadow-md">
                  <JsonView
                    displaySize
                    enableClipboard
                    src={event.failure}
                    className="text-sm"
                    theme="atom"
                  />
                </div>
              </div>
            </AccordionContent>
          </AccordionItem>
        )}
        {/* Action details */}
        {(event?.result as Record<string, unknown>) && (
          <AccordionItem value="result">
            <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
              <div className="flex items-end">
                <span>Event Result</span>
              </div>
            </AccordionTrigger>
            <AccordionContent>
              <div className="my-4 flex flex-col space-y-8 px-4">
                <div className="rounded-md border p-4 shadow-md">
                  <JsonView
                    displaySize
                    enableClipboard
                    src={(event.result as Record<string, unknown>) ?? {}}
                    className="text-sm"
                    theme="atom"
                  />
                </div>
              </div>
            </AccordionContent>
          </AccordionItem>
        )}

        {isDSLRunArgs(event.event_group?.action_input) && (
          <AccordionItem value="result">
            <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
              <div className="flex items-end">
                <span>Input</span>
              </div>
            </AccordionTrigger>
            <AccordionContent>
              <div className="my-4 flex flex-col space-y-8 px-4">
                <div className="rounded-md border p-4 shadow-md">
                  <JsonView
                    displaySize
                    enableClipboard
                    src={event?.event_group?.action_input}
                    className="text-sm"
                    theme="atom"
                  />
                </div>
              </div>
            </AccordionContent>
          </AccordionItem>
        )}

        {isUDFActionInput(event.event_group?.action_input) && (
          <>
            <AccordionItem value="result">
              <AccordionTrigger className="px-4 text-xs font-bold tracking-wide">
                <div className="flex items-end">
                  <span>Input</span>
                </div>
              </AccordionTrigger>
              <AccordionContent>
                <div className="my-4 flex flex-col space-y-8 px-4">
                  <div className="rounded-md border p-4 shadow-md">
                    <JsonView
                      displaySize
                      enableClipboard
                      src={event.event_group.action_input}
                      className="text-sm"
                      theme="atom"
                    />
                  </div>
                </div>
              </AccordionContent>
            </AccordionItem>
          </>
        )}
      </Accordion>
    </div>
  )
}

export function EventGeneralInfo({ event }: { event: EventHistoryResponse }) {
  const { event_group } = event
  const formattedEventType = parseEventType(event.event_type)
  const eventTimeDate = new Date(event.event_time)
  return (
    <div className="my-4 flex flex-col space-y-2 px-4">
      <div className="flex w-full items-center space-x-4">
        {event_group?.udf_key ? (
          getIcon(event_group.udf_key, {
            className: "size-10 p-2",
            flairsize: "md",
          })
        ) : (
          <GenericWorkflowIcon className="size-10 p-2" />
        )}
        <div className="flex w-full flex-1 justify-between space-x-12">
          <div className="flex flex-col">
            <div className="text-md flex w-full items-center justify-between font-medium leading-none">
              <div className="flex w-full">
                {event_group?.action_title || formattedEventType}
              </div>
            </div>
          </div>
        </div>
      </div>
      <div className="space-x-2">
        <Label className="text-xs text-muted-foreground">Event Type</Label>
        <DescriptorBadge
          text={formattedEventType}
          className={cn(
            "bg-gray-100/80",
            ERROR_EVENT_TYPES.includes(event.event_type) && "bg-rose-100",
            event.event_type == "WORKFLOW_EXECUTION_STARTED" &&
              "bg-emerald-100",
            event.event_type == "WORKFLOW_EXECUTION_COMPLETED" &&
              "bg-emerald-200",
            event.event_type == "ACTIVITY_TASK_SCHEDULED" && "bg-amber-100",
            event.event_type == "ACTIVITY_TASK_STARTED" && "bg-sky-200/70",
            event.event_type == "ACTIVITY_TASK_COMPLETED" && "bg-sky-200/70",
            event.event_type == "START_CHILD_WORKFLOW_EXECUTION_INITIATED" &&
              "bg-amber-100",
            event.event_type == "CHILD_WORKFLOW_EXECUTION_STARTED" &&
              "bg-violet-200/70",
            event.event_type == "CHILD_WORKFLOW_EXECUTION_COMPLETED" &&
              "bg-violet-200/70",
            event.event_type == "CHILD_WORKFLOW_EXECUTION_FAILED" &&
              "bg-rose-200"
          )}
        />
      </div>
      <div className="space-x-2">
        <Label className="w-24 text-xs text-muted-foreground">Event ID</Label>
        <DescriptorBadge text={event.event_id.toString()} />
      </div>
      <div className="space-x-2">
        <Label className="w-24 text-xs text-muted-foreground">Event Time</Label>
        <DescriptorBadge
          text={
            eventTimeDate.toLocaleString() +
            " (" +
            getRelativeTime(eventTimeDate) +
            ")"
          }
        />
      </div>
      <div className="space-x-2">
        {event.role?.type && (
          <>
            <Label className="w-24 text-xs text-muted-foreground">
              Triggered By
            </Label>
            <DescriptorBadge text={event.role?.type} className="capitalize" />
          </>
        )}
      </div>
      {/* Action event group fields */}
      <div className="space-x-2">
        {event_group?.udf_key && (
          <>
            <Label className="w-24 text-xs text-muted-foreground">
              Action Type
            </Label>
            <DescriptorBadge
              text={event_group.udf_key}
              className="font-mono font-semibold tracking-tight"
            />
          </>
        )}
      </div>
      <div className="space-x-2">
        {event_group?.udf_key && (
          <>
            <Label className="w-24 text-xs text-muted-foreground">
              Description
            </Label>
            <DescriptorBadge
              text={event_group.action_description || "No description"}
              className="font-semibold"
            />
          </>
        )}
      </div>

      {isUDFActionInput(event_group?.action_input) && (
        <ActionEventGeneralInfo input={event_group.action_input} />
      )}
      {isDSLRunArgs(event_group?.action_input) && (
        <ChildWorkflowEventGeneralInfo input={event_group.action_input} />
      )}
    </div>
  )
}

export function DescriptorBadge({
  text,
  className,
}: { text: string } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <Badge
      variant="secondary"
      className={cn("bg-indigo-50 text-foreground/60", className)}
    >
      {text}
    </Badge>
  )
}
function ChildWorkflowEventGeneralInfo({ input }: { input: DSLRunArgs }) {
  return (
    <div className="space-x-2">
      <Label className="w-24 text-xs text-muted-foreground">
        Workflow Title
      </Label>
      <DescriptorBadge
        text={input.dsl.title}
        className="font-mono font-semibold"
      />
    </div>
  )
}

function ActionEventGeneralInfo({ input }: { input: UDFActionInput }) {
  return (
    <div>
      <div className="space-x-2">
        {input.task.depends_on && (
          <>
            <Label className="w-24 text-xs text-muted-foreground">
              Dependencies
            </Label>
            {input.task.depends_on?.map((dep) => (
              <DescriptorBadge
                key={dep}
                text={dep}
                className="font-mono font-semibold"
              />
            ))}
          </>
        )}
      </div>
      <div className="space-x-2">
        {input.task.run_if && (
          <>
            <Label className="w-24 text-xs text-muted-foreground">Run If</Label>
            <DescriptorBadge
              text={input.task.run_if}
              className="font-mono font-semibold tracking-tight"
            />
          </>
        )}
      </div>
      <div className="space-x-2">
        {input.task.for_each && (
          <>
            <Label className="w-24 text-xs text-muted-foreground">
              For Each
            </Label>
            {(Array.isArray(input.task.for_each)
              ? input.task.for_each
              : [input.task.for_each ?? []]
            ).map((dep, index) => (
              <DescriptorBadge
                key={`${dep}-${index}`}
                text={dep}
                className="font-mono font-semibold"
              />
            ))}
          </>
        )}
      </div>
    </div>
  )
}

function isUDFActionInput(actionInput: unknown): actionInput is UDFActionInput {
  return (
    typeof actionInput === "object" &&
    actionInput !== null &&
    "task" in actionInput &&
    typeof (actionInput as UDFActionInput).task === "object"
  )
}

function isDSLRunArgs(actionInput: unknown): actionInput is DSLRunArgs {
  // Define the conditions to check for DSLRunArgs
  return (
    typeof actionInput === "object" &&
    actionInput !== null &&
    // Check specific properties of DSLRunArgs
    typeof (actionInput as DSLRunArgs).dsl === "object" &&
    (actionInput as DSLRunArgs).wf_id !== undefined
  )
}
