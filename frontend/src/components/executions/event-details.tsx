import React from "react"
import { DSLRunArgs, RunActionInput, WorkflowExecutionEvent } from "@/client"
import JsonView from "react18-json-view"

import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"

import "react18-json-view/src/style.css"

import Link from "next/link"
import { useParams } from "next/navigation"
import {
  InfoIcon,
  SquareArrowOutUpRightIcon,
  TriangleAlert,
} from "lucide-react"

import {
  ERROR_EVENT_TYPES,
  getRelativeTime,
  isDSLRunArgs,
  isInteractionInput,
  isRunActionInput,
  parseEventType,
  parseExecutionId,
} from "@/lib/event-history"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { CodeBlock } from "@/components/code-block"
import { GenericWorkflowIcon, getIcon } from "@/components/icons"

/**
 * Event history for a specific workflow execution
 * @param param0
 * @returns
 */
export function WorkflowExecutionEventDetailView({
  event,
}: {
  event: WorkflowExecutionEvent
}) {
  const { event_group, result, failure } = event
  const action_input = event_group?.action_input
  return (
    <div className="size-full overflow-auto">
      {/* Metadata */}
      <Accordion
        type="multiple"
        defaultValue={["general", "execution-context", "failure", "result"]}
      >
        {/* General */}
        <AccordionItem value="general">
          <AccordionTrigger className="px-4 text-xs font-bold">
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
        {failure && (
          <AccordionItem value="failure">
            <AccordionTrigger className="px-4 text-xs font-bold">
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
                <CodeBlock title="Message">{failure.message}</CodeBlock>
              </div>
            </AccordionContent>
          </AccordionItem>
        )}

        {/* Action details */}
        {(result as Record<string, unknown>) && (
          <AccordionItem value="result">
            <AccordionTrigger className="px-4 text-xs font-bold">
              <div className="flex items-end">
                <span>Event Result</span>
              </div>
            </AccordionTrigger>
            <AccordionContent>
              <div className="my-4 flex flex-col space-y-8 px-4">
                <JsonViewWithControls src={result} />
              </div>
            </AccordionContent>
          </AccordionItem>
        )}

        {isDSLRunArgs(action_input) && (
          <AccordionItem value="result">
            <AccordionTrigger className="px-4 text-xs font-bold">
              <div className="flex items-end">
                <span>Child Workflow Input</span>
              </div>
            </AccordionTrigger>
            <AccordionContent>
              <div className="my-4 flex flex-col space-y-8 px-4">
                <JsonViewWithControls src={action_input} />
              </div>
            </AccordionContent>
          </AccordionItem>
        )}

        {isRunActionInput(action_input) && (
          <AccordionItem value="result">
            <AccordionTrigger className="px-4 text-xs font-bold">
              <div className="flex items-end">
                <span>Action Input</span>
              </div>
            </AccordionTrigger>
            <AccordionContent>
              <div className="my-4 flex flex-col space-y-8 px-4">
                <JsonViewWithControls src={action_input} />
              </div>
            </AccordionContent>
          </AccordionItem>
        )}

        {isInteractionInput(action_input) && (
          <AccordionItem value="result">
            <AccordionTrigger className="px-4 text-xs font-bold">
              <div className="flex items-end">
                <span>Received Input</span>
              </div>
            </AccordionTrigger>
            <AccordionContent>
              <div className="my-4 flex flex-col space-y-8 px-4">
                <JsonViewWithControls src={action_input} />
              </div>
            </AccordionContent>
          </AccordionItem>
        )}
      </Accordion>
    </div>
  )
}

export function EventGeneralInfo({ event }: { event: WorkflowExecutionEvent }) {
  const {
    event_group,
    role,
    event_type,
    event_id,
    parent_wf_exec_id,
    workflow_timeout,
  } = event
  const {
    udf_key,
    action_title,
    action_description,
    retry_policy: action_retry_policy,
    action_input,
    join_strategy,
    start_delay,
    related_wf_exec_id,
  } = event_group || {}
  const formattedEventType = parseEventType(event_type)
  const eventTimeDate = new Date(event.event_time)
  const { max_attempts, timeout } = action_retry_policy || {}

  // Construct the link within the same workspace to the related workflow execution
  const { workspaceId } = useParams()
  let relatedWorkflowExecutionLink: string | undefined
  if (related_wf_exec_id) {
    const [relatedWorkflowId, relatedExecutionId] =
      parseExecutionId(related_wf_exec_id)

    relatedWorkflowExecutionLink = `/workspaces/${workspaceId}/workflows/${relatedWorkflowId}/executions/${relatedExecutionId}`
  }

  let parentWorkflowExecutionLink: string | undefined
  if (parent_wf_exec_id) {
    const [parentWorkflowId, parentExecutionId] =
      parseExecutionId(parent_wf_exec_id)
    parentWorkflowExecutionLink = `/workspaces/${workspaceId}/workflows/${parentWorkflowId}/executions/${parentExecutionId}`
  }

  return (
    <div className="my-4 flex flex-col space-y-2 px-4">
      <div className="flex w-full items-center space-x-4">
        {udf_key ? (
          getIcon(udf_key, {
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
                {action_title || formattedEventType}
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
            ERROR_EVENT_TYPES.includes(event_type) && "bg-rose-100",
            event_type == "WORKFLOW_EXECUTION_STARTED" && "bg-emerald-100",
            event_type == "WORKFLOW_EXECUTION_COMPLETED" && "bg-emerald-200",
            event_type == "ACTIVITY_TASK_SCHEDULED" && "bg-amber-100",
            event_type == "ACTIVITY_TASK_STARTED" && "bg-sky-200/70",
            event_type == "ACTIVITY_TASK_COMPLETED" && "bg-sky-200/70",
            event_type == "START_CHILD_WORKFLOW_EXECUTION_INITIATED" &&
              "bg-amber-100",
            event_type == "CHILD_WORKFLOW_EXECUTION_STARTED" &&
              "bg-violet-200/70",
            event_type == "CHILD_WORKFLOW_EXECUTION_COMPLETED" &&
              "bg-violet-200/70",
            event_type == "CHILD_WORKFLOW_EXECUTION_FAILED" && "bg-rose-200"
          )}
        />
        {event_type.includes("CHILD_WORKFLOW_EXECUTION") &&
          related_wf_exec_id &&
          relatedWorkflowExecutionLink && (
            <Tooltip>
              <TooltipTrigger>
                <Badge variant="outline">
                  <Link href={relatedWorkflowExecutionLink}>
                    <div className="flex items-center gap-1">
                      <span className="font-normal">Go to workflow run</span>
                      <SquareArrowOutUpRightIcon className="size-3" />
                    </div>
                  </Link>
                </Badge>
              </TooltipTrigger>
              <TooltipContent>
                <span>{related_wf_exec_id}</span>
              </TooltipContent>
            </Tooltip>
          )}
        {event_type == "WORKFLOW_EXECUTION_STARTED" &&
          parent_wf_exec_id &&
          parentWorkflowExecutionLink && (
            <Tooltip>
              <TooltipTrigger>
                <Badge variant="outline">
                  <Link href={parentWorkflowExecutionLink}>
                    <div className="flex items-center gap-1">
                      <span className="font-normal">
                        Go to parent workflow run
                      </span>
                      <SquareArrowOutUpRightIcon className="size-3" />
                    </div>
                  </Link>
                </Badge>
              </TooltipTrigger>
              <TooltipContent>
                <span>{parent_wf_exec_id}</span>
              </TooltipContent>
            </Tooltip>
          )}
      </div>
      <div className="space-x-2">
        <Label className="w-24 text-xs text-muted-foreground">Event ID</Label>
        <DescriptorBadge text={event_id.toString()} />
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
        {role?.type && (
          <>
            <Label className="w-24 text-xs text-muted-foreground">
              Triggered By
            </Label>
            <DescriptorBadge text={role.type} className="capitalize" />
          </>
        )}
      </div>
      {event_type == "WORKFLOW_EXECUTION_STARTED" && workflow_timeout && (
        <div className="space-x-2">
          <Label className="w-24 text-xs text-muted-foreground">
            Workflow Timeout
          </Label>
          <DescriptorBadge text={`${workflow_timeout}s`} />
        </div>
      )}

      {/* Action event group fields */}
      <div className="space-x-2">
        {udf_key && (
          <>
            <Label className="w-24 text-xs text-muted-foreground">
              Action Type
            </Label>
            <DescriptorBadge
              text={udf_key}
              className="font-mono font-semibold tracking-tight"
            />
          </>
        )}
      </div>
      <div className="space-x-2">
        {udf_key && (
          <>
            <Label className="w-24 text-xs text-muted-foreground">
              Description
            </Label>
            <DescriptorBadge
              text={action_description || "No description"}
              className="font-semibold"
            />
          </>
        )}
      </div>

      {/* Action Retry policy */}
      {action_retry_policy && (
        <div className="space-x-2">
          <Label className="w-24 text-xs text-muted-foreground">
            Retry Policy
          </Label>
          <DescriptorBadge
            className="font-mono"
            text={`${max_attempts && max_attempts > 0 ? `Max ${max_attempts} attempt(s)` : "Unlimited attempts"}${timeout ? `, ${timeout}s timeout` : ""}`}
          />
        </div>
      )}

      {/* Start delay */}
      {start_delay !== undefined && start_delay > 0 && (
        <div className="space-x-2">
          <Label className="w-24 text-xs text-muted-foreground">
            Start Delay
          </Label>
          <DescriptorBadge
            className="font-mono"
            text={`${start_delay.toFixed(1)}s`}
          />
        </div>
      )}

      {/* Join policy */}
      {join_strategy && (
        <div className="space-x-2">
          <Label className="w-24 text-xs text-muted-foreground">
            Join Strategy
          </Label>
          <DescriptorBadge className="font-mono" text={join_strategy} />
        </div>
      )}
      {isRunActionInput(action_input) && (
        <ActionEventGeneralInfo input={action_input} />
      )}
      {isDSLRunArgs(action_input) && (
        <ChildWorkflowEventGeneralInfo input={action_input} />
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
      className={cn("bg-muted-foreground/5 text-foreground/60", className)}
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
        text={input.dsl?.title || "No title"}
        className="font-mono font-semibold"
      />
    </div>
  )
}

function ActionEventGeneralInfo({
  input: {
    task: { depends_on, run_if, for_each },
  },
}: {
  input: RunActionInput
}) {
  return (
    <div>
      <div className="space-x-2">
        {depends_on && depends_on.length > 0 && (
          <>
            <Label className="w-24 text-xs text-muted-foreground">
              Dependencies ({depends_on.length})
            </Label>
            {depends_on.map((dep) => (
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
        {run_if && (
          <>
            <Label className="w-24 text-xs text-muted-foreground">Run If</Label>
            <DescriptorBadge
              text={run_if}
              className="font-mono font-semibold tracking-tight"
            />
          </>
        )}
      </div>
      <div className="space-x-2">
        {for_each && (
          <>
            <Label className="w-24 text-xs text-muted-foreground">
              For Each
            </Label>
            {(Array.isArray(for_each) ? for_each : [for_each ?? []]).map(
              (dep, index) => (
                <DescriptorBadge
                  key={`${dep}-${index}`}
                  text={dep}
                  className="font-mono font-semibold"
                />
              )
            )}
          </>
        )}
      </div>
    </div>
  )
}

function JsonViewWithControls({
  src,
  title = "JSON",
}: {
  src: unknown
  title?: string
}): JSX.Element {
  const [isExpanded, setIsExpanded] = React.useState(false)
  return (
    <div className="space-y-2">
      <div className="flex w-full items-center gap-4">
        <span className="text-xs font-semibold text-foreground/50">
          {title}
        </span>
        <div className="flex items-center gap-2">
          <Switch
            checked={isExpanded}
            onCheckedChange={setIsExpanded}
            className="data-[state=checked]:bg-muted-foreground"
          />
          <p className="text-xs text-foreground/70">
            {isExpanded ? "Collapse" : "Expand"}
          </p>
        </div>
      </div>
      <div className="rounded-md border bg-muted-foreground/5 p-4">
        <JsonView
          collapsed={!isExpanded}
          displaySize
          enableClipboard
          src={src}
          className="break-all text-xs"
          theme="atom"
        />
      </div>
    </div>
  )
}
