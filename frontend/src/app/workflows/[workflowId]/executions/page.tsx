"use client"

import React from "react"
import { EventHistoryResponse, WorkflowExecutionResponse } from "@/client"
import { useWorkflow } from "@/providers/workflow"
import {
  BanIcon,
  CalendarCheck,
  CircleCheck,
  CircleX,
  GlobeIcon,
  Loader2,
  Play,
} from "lucide-react"
import { ImperativePanelHandle } from "react-resizable-panels"

import {
  useWorkflowExecutionEventHistory,
  useWorkflowExecutions,
} from "@/lib/hooks"
import { cn, undoSlugify } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import { Button, buttonVariants } from "@/components/ui/button"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Label } from "@/components/ui/label"
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { CenteredSpinner } from "@/components/loading/spinner"
import NoContent from "@/components/no-content"
import { AlertNotification } from "@/components/notifications"

const ERROR_EVENT_TYPES: EventHistoryResponse["event_type"][] = [
  "WORKFLOW_EXECUTION_FAILED",
  "ACTIVITY_TASK_FAILED",
] as const

export type ActivityTaskScheduledEventDetails = {
  activityType: { name: string }
  input: Record<string, any>
}

export default function WorkflowExecutionsPage() {
  const { workflowId } = useWorkflow()
  return (
    <div className="flex h-screen flex-col overflow-auto">
      <div className="flex-1 space-y-8">
        {workflowId ? (
          <WorkflowExecutionsViewLayout
            workflowId={workflowId}
            navCollapsedSize={10}
          />
        ) : (
          <div>Loading...</div>
        )}
      </div>
    </div>
  )
}

export function WorkflowExecutionsViewLayout({
  workflowId,
  defaultLayout = [10, 10, 80],
  defaultCollapsed = false,
  navCollapsedSize,
}: {
  workflowId: string
  defaultLayout?: number[]
  defaultCollapsed?: boolean
  navCollapsedSize: number
}) {
  const sidePanelRef = React.useRef<ImperativePanelHandle>(null)
  const [isCollapsed, setIsCollapsed] = React.useState(defaultCollapsed)
  const {
    workflowExecutions,
    workflowExecutionsError,
    workflowExecutionsIsLoading,
  } = useWorkflowExecutions(workflowId)

  const [executionId, setExecutionId] = React.useState<string | undefined>(
    undefined
  )

  // Adjust onCollapse to match the expected signature
  const handleCollapse = () => {
    // Assuming you have a way to set the collapsed state here
    setIsCollapsed(true) // Set to true when you know the panel is collapsed
    document.cookie = `workflow-executions:react-resizable-panels:collapsed=${JSON.stringify(true)}`
  }

  // Adjust onExpand to match the expected signature
  const handleExpand = () => {
    // Assuming you have a way to set the collapsed state here
    setIsCollapsed(false) // Set to false when you know the panel is expanded
    document.cookie = `rworkflow-executions:eact-resizable-panels:collapsed=${JSON.stringify(false)}`
  }

  if (workflowExecutionsIsLoading) {
    return <CenteredSpinner />
  }
  if (workflowExecutionsError) {
    return <AlertNotification message={workflowExecutionsError.message} />
  }

  return (
    <TooltipProvider delayDuration={0}>
      <ResizablePanelGroup
        className="h-full"
        direction="horizontal"
        onLayout={(sizes: number[]) => {
          document.cookie = `rworkflow-executions:eact-resizable-panels:layout=${JSON.stringify(
            sizes
          )}`
        }}
      >
        {/* All executions */}
        <ResizablePanel
          ref={sidePanelRef}
          defaultSize={defaultLayout[0]}
          collapsedSize={navCollapsedSize}
          collapsible={true}
          minSize={12}
          maxSize={20}
          onCollapse={handleCollapse}
          onExpand={handleExpand}
          className={cn("flex h-full flex-col p-2", isCollapsed && "min-w-14")}
        >
          <ScrollArea className="overflow-auto">
            <SectionHead text="Workflow Executions" />
            <WorkflowExecutionNav
              executions={workflowExecutions}
              executionId={executionId}
              setExecutionId={setExecutionId}
            />
          </ScrollArea>
        </ResizablePanel>
        <ResizableHandle withHandle />

        {/* For items that should align at the end of the side nav */}
        <ResizablePanel
          defaultSize={defaultLayout[1]}
          minSize={10}
          className={cn("flex h-full flex-col p-2", isCollapsed && "min-w-14")}
        >
          <ScrollArea className="overflow-auto">
            <SectionHead text={executionId?.split(":")[1] ?? "Event History"} />
            {executionId ? (
              <WorkflowExecutionEventHistory executionId={executionId} />
            ) : (
              <span className="flex justify-center p-4 text-center text-xs text-muted-foreground">
                Select a Workflow Execution.
              </span>
            )}
          </ScrollArea>
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel
          defaultSize={defaultLayout[2]}
          minSize={25}
        ></ResizablePanel>
      </ResizablePanelGroup>
    </TooltipProvider>
  )
}

/**
 * The top-level view of workflow executions (shows each execution and its status)
 * @param param0
 * @returns
 */
function WorkflowExecutionNav({
  executions: workflowExecutions,
  executionId,
  setExecutionId,
}: {
  executions?: WorkflowExecutionResponse[]
  isCollapsed?: boolean
  executionId?: string
  setExecutionId: (id: string) => void
}) {
  if (!workflowExecutions) {
    return <NoContent message="No workflow executions found." />
  }

  return (
    <div
      data-collapsed={false}
      className="group flex flex-col gap-4 py-2 data-[collapsed=true]:py-2"
    >
      <nav className="grid gap-1 px-2 group-[[data-collapsed=true]]:justify-center group-[[data-collapsed=true]]:px-2">
        {workflowExecutions.map((execution, index) => (
          <HoverCard openDelay={100} closeDelay={100}>
            <HoverCardTrigger asChild>
              <Button
                key={index}
                className={cn(
                  buttonVariants({ variant: "default", size: "sm" }),
                  "justify-start bg-background text-muted-foreground shadow-none hover:cursor-default hover:bg-gray-100",
                  execution.id === executionId && "bg-gray-200"
                )}
                onClick={() => setExecutionId(execution.id)}
              >
                <WorkflowExecutionStatusIcon
                  status={execution.status}
                  className="size-4"
                />
                <span className="ml-2">
                  {new Date(execution.start_time).toLocaleString()}
                </span>
              </Button>
            </HoverCardTrigger>
            <HoverCardContent className="w-100" side="right">
              <div className="flex flex-col items-start justify-between space-y-2 text-start text-xs">
                <div className="flex flex-col">
                  <Label className="text-xs text-muted-foreground">
                    Execution ID
                  </Label>
                  <span>{execution.id.split(":")[1]}</span>
                </div>
                <div className="flex flex-col">
                  <Label className="text-xs text-muted-foreground">
                    Run ID
                  </Label>
                  <span>{execution.run_id}</span>
                </div>
                <div className="flex flex-col">
                  <Label className="text-xs text-muted-foreground">
                    Start Time
                  </Label>
                  <span>{new Date(execution.start_time).toLocaleString()}</span>
                </div>
                <div className="flex flex-col">
                  <Label className="text-xs text-muted-foreground">
                    End Time
                  </Label>
                  <span>{new Date(execution.close_time).toLocaleString()}</span>
                </div>
              </div>
            </HoverCardContent>
          </HoverCard>
        ))}
      </nav>
    </div>
  )
}

/**
 * The top-level view of workflow executions (shows each execution and its status)
 * @param param0
 * @returns
 */
function WorkflowExecutionEventHistory({
  executionId,
}: {
  executionId: string
}) {
  const { eventHistory, eventHistoryLoading, eventHistoryError } =
    useWorkflowExecutionEventHistory(executionId)

  const [eventId, setEventId] = React.useState<number | undefined>(undefined)

  if (eventHistoryLoading) {
    return <CenteredSpinner />
  }
  if (eventHistoryError) {
    return <AlertNotification message={eventHistoryError.message} />
  }
  if (!eventHistory) {
    return <NoContent message="No event history found." />
  }
  return (
    <div className="group flex flex-col gap-4 py-2">
      <nav className="grid gap-1 px-2">
        {eventHistory.map((event, index) => (
          <Button
            key={index}
            className={cn(
              buttonVariants({ variant: "default", size: "sm" }),
              "justify-start space-x-1 bg-background text-muted-foreground shadow-none hover:cursor-default hover:bg-gray-100",
              event.event_id === eventId && "bg-gray-200",
              ERROR_EVENT_TYPES.includes(event.event_type) &&
                "bg-red-100 hover:bg-red-200"
            )}
            onClick={() => setEventId(event.event_id)}
          >
            <div className="flex items-center justify-items-start">
              <div className="flex w-10">
                <Badge
                  variant="outline"
                  className="max-w-10 flex-none rounded-md p-1 text-xs font-light text-muted-foreground"
                >
                  {event.event_id}
                </Badge>
              </div>
              <EventHistoryItemIcon
                eventType={event.event_type}
                className="size-4 w-8 flex-none"
              />

              <span className="text-xs text-muted-foreground">
                {getEventDescriptor(event)}
              </span>
            </div>
          </Button>
        ))}
      </nav>
    </div>
  )
}

function getEventDescriptor(event: EventHistoryResponse) {
  switch (event.event_type) {
    case "WORKFLOW_EXECUTION_STARTED":
      return "Workflow Execution Started"
    case "WORKFLOW_EXECUTION_COMPLETED":
      return "Workflow Execution Completed"
    case "WORKFLOW_EXECUTION_FAILED":
      return "Workflow Execution Failed"
    case "ACTIVITY_TASK_SCHEDULED":
      const details = event.details as ActivityTaskScheduledEventDetails
      // This returns the UDF key, slugified.
      // Split the key on duner __ and undo the slugify
      const activityName = details.activityType.name
        .split("__")
        .map((s) => undoSlugify(s).toLowerCase())
        .join(".")
      return activityName
    case "ACTIVITY_TASK_STARTED":
      return "Activity Task Started"
    case "ACTIVITY_TASK_COMPLETED":
      return "Activity Task Completed"
    case "ACTIVITY_TASK_FAILED":
      return "Activity Task Failed"
    default:
      return "Unknown event history type, please check the logs"
  }
}

export function WorkflowExecutionStatusIcon({
  status,
  className,
}: {
  status: WorkflowExecutionResponse["status"]
} & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {getExecutionStatusIcon(status, className)}
      </TooltipTrigger>
      <TooltipContent side="top" className="flex items-center gap-4  shadow-lg">
        <span>{undoSlugify(status.toLowerCase())}</span>
      </TooltipContent>
    </Tooltip>
  )
}

function SectionHead({ text }: { text: string }) {
  return (
    <span className="flex w-full justify-center rounded-md border px-2 py-1 text-center text-sm font-normal text-muted-foreground/80">
      {text}
    </span>
  )
}

export function EventHistoryItemIcon({
  eventType,
  className,
}: {
  eventType: EventHistoryResponse["event_type"]
} & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {getEventHistoryIcon(eventType, className)}
      </TooltipTrigger>
      <TooltipContent side="top" className="flex items-center gap-4  shadow-lg">
        <span>{undoSlugify(eventType.toLowerCase())}</span>
      </TooltipContent>
    </Tooltip>
  )
}

function getEventHistoryIcon(
  eventType: EventHistoryResponse["event_type"],
  className?: string
) {
  switch (eventType) {
    case "WORKFLOW_EXECUTION_STARTED":
      return <GlobeIcon className={cn("stroke-emerald-500", className)} />
    case "WORKFLOW_EXECUTION_COMPLETED":
      return (
        <CircleCheck
          className={cn("fill-emerald-500 stroke-white", className)}
        />
      )
    case "WORKFLOW_EXECUTION_FAILED":
      return <CircleX className={cn("fill-rose-500 stroke-white", className)} />
    case "ACTIVITY_TASK_SCHEDULED":
      return (
        <CalendarCheck
          className={cn("fill-white stroke-orange-500/70", className)}
        />
      )
    case "ACTIVITY_TASK_STARTED":
      return <Play className={cn("fill-rose-500 stroke-white", className)} />
    case "ACTIVITY_TASK_COMPLETED":
      return (
        <CircleCheck className={cn("fill-sky-500 stroke-white", className)} />
      )
    case "ACTIVITY_TASK_FAILED":
      return <CircleX className={cn("fill-rose-500 stroke-white", className)} />
    default:
      throw new Error("Invalid event type")
  }
}

export function getExecutionStatusIcon(
  status: WorkflowExecutionResponse["status"],
  className?: string
) {
  switch (status) {
    case "COMPLETED":
      return (
        <CircleCheck
          className={cn(
            "border-none border-emerald-500 fill-emerald-500 stroke-white",
            className
          )}
        />
      )
    case "FAILED":
      return <CircleX className={cn("fill-rose-500 stroke-white", className)} />
    case "RUNNING":
      return (
        <Loader2 className={cn("animate-spin stroke-blue-500/50", className)} />
      )
    case "TERMINATED":
      return (
        <BanIcon
          className={cn("fill-orange-500/50 stroke-orange-700", className)}
        />
      )
    case "CANCELED":
      return (
        <CircleX className={cn("fill-orange-500 stroke-white", className)} />
      )
    default:
      throw new Error("Invalid status")
  }
}
