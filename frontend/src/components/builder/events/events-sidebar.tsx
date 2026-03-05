"use client"

import {
  AlarmClockOffIcon,
  CalendarSearchIcon,
  CircleArrowRightIcon,
  CircleCheck,
  CircleMinusIcon,
  CircleX,
  FileInputIcon,
  GitBranchIcon,
  MessagesSquare,
  ShapesIcon,
  TimerResetIcon,
  WorkflowIcon,
} from "lucide-react"
import { useEffect, useRef, useState } from "react"
import type { ImperativePanelHandle } from "react-resizable-panels"
import {
  $TriggerType,
  type TriggerType,
  type WorkflowExecutionReadMinimal,
} from "@/client"
import { ActionEventPane } from "@/components/builder/events/events-selected-action"
import { EventsSidebarEmpty } from "@/components/builder/events/events-sidebar-empty"
import { WorkflowInteractions } from "@/components/builder/events/events-sidebar-interactions"
import {
  getTriggerTypeIcon,
  WorkflowEvents,
  WorkflowEventsHeader,
} from "@/components/builder/events/events-workflow"
import { Spinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useLocalStorage } from "@/hooks/use-local-storage"
import {
  useCompactWorkflowExecution,
  useOrgAppSettings,
  useWorkflowExecutions,
} from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"

// Define the available trigger types for UI generation
const AVAILABLE_TRIGGER_TYPES: readonly TriggerType[] = $TriggerType.enum
const DETACHED_EXECUTION_FALLBACK_GRACE_MS = 15_000

function formatExecutionTime(timestamp: string): string {
  return new Date(timestamp).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  })
}

function formatExecutionStatus(
  status: WorkflowExecutionReadMinimal["status"]
): string {
  return status
    .toLowerCase()
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ")
}

function getExecutionStatusIcon(
  status: WorkflowExecutionReadMinimal["status"],
  className?: string
) {
  switch (status) {
    case "RUNNING":
      return <Spinner className={cn("size-3", className)} />
    case "COMPLETED":
      return (
        <CircleCheck
          className={cn("size-3 fill-emerald-500 stroke-white", className)}
        />
      )
    case "FAILED":
      return (
        <CircleX
          className={cn("size-3 fill-rose-500 stroke-white", className)}
        />
      )
    case "CANCELED":
      return (
        <CircleMinusIcon
          className={cn("size-3 fill-orange-500 stroke-white", className)}
        />
      )
    case "TERMINATED":
      return (
        <CircleMinusIcon
          className={cn("size-3 fill-rose-500 stroke-white", className)}
        />
      )
    case "CONTINUED_AS_NEW":
      return (
        <CircleArrowRightIcon
          className={cn("size-3 fill-blue-500 stroke-white", className)}
        />
      )
    case "TIMED_OUT":
      return (
        <AlarmClockOffIcon
          className={cn("size-3 stroke-rose-500", className)}
          strokeWidth={2.25}
        />
      )
    default:
      return (
        <GitBranchIcon
          className={cn("size-3 text-muted-foreground", className)}
        />
      )
  }
}

function getExecutionStatusTextColor(
  status: WorkflowExecutionReadMinimal["status"]
): string {
  switch (status) {
    case "RUNNING":
      return "text-blue-600 dark:text-blue-400"
    case "COMPLETED":
      return "text-emerald-600 dark:text-emerald-400"
    case "FAILED":
    case "TERMINATED":
    case "TIMED_OUT":
      return "text-rose-600 dark:text-rose-400"
    case "CANCELED":
      return "text-orange-600 dark:text-orange-400"
    case "CONTINUED_AS_NEW":
      return "text-blue-600 dark:text-blue-400"
    default:
      return "text-muted-foreground"
  }
}

function ExecutionOptionRow({
  execution,
}: {
  execution: WorkflowExecutionReadMinimal
}) {
  return (
    <div className="flex min-w-0 items-center gap-2 overflow-hidden whitespace-nowrap">
      <div className="shrink-0">
        {getTriggerTypeIcon(execution.trigger_type)}
      </div>
      <span className="truncate text-xs font-medium text-foreground">
        {formatExecutionTime(execution.start_time)}
      </span>
      <span className="shrink-0 text-muted-foreground/60">•</span>
      <span className="flex shrink-0 items-center gap-1 text-[11px]">
        {getExecutionStatusIcon(execution.status)}
        <span
          className={cn(
            "hidden sm:inline",
            getExecutionStatusTextColor(execution.status)
          )}
        >
          {formatExecutionStatus(execution.status)}
        </span>
      </span>
      <span className="shrink-0 text-muted-foreground/60">•</span>
      <span className="shrink-0 font-mono text-[10px] text-muted-foreground">
        {execution.id.slice(0, 8)}
      </span>
    </div>
  )
}

export type EventsSidebarTabs =
  | "workflow-events"
  | "action-input"
  | "action-result"
  | "action-interaction"
/**
 * Interface for controlling the events sidebar through a ref
 */
export interface EventsSidebarRef extends ImperativePanelHandle {
  /** Sets the active tab in the events sidebar */
  setActiveTab: (tab: EventsSidebarTabs) => void
  /** Gets the current active tab */
  getActiveTab: () => EventsSidebarTabs
  /** Sets the open state of the events sidebar */
  setOpen: (open: boolean) => void
  /** Gets the open state of the events sidebar */
  isOpen: () => boolean
}

export function BuilderSidebarEvents() {
  const { workflowId } = useWorkflow()
  const { sidebarRef, currentExecutionId, setCurrentExecutionId } =
    useWorkflowBuilder()
  const detachedSelectionIdRef = useRef<string | null>(null)
  const detachedSelectionStartedAtRef = useRef<number | null>(null)
  const [activeTab, setActiveTab] =
    useState<EventsSidebarTabs>("workflow-events")
  const [open, setOpen] = useState(false)
  const [selectedTriggerTypes] = useLocalStorage<TriggerType[]>(
    "selected-trigger-types",
    [...AVAILABLE_TRIGGER_TYPES]
  )

  const {
    workflowExecutions,
    workflowExecutionsIsLoading,
    workflowExecutionsError,
  } = useWorkflowExecutions(workflowId)
  const filteredExecutions = (workflowExecutions ?? []).filter((execution) =>
    selectedTriggerTypes.includes(execution.trigger_type)
  )
  const currentExecutionInFilteredList = Boolean(
    currentExecutionId &&
      filteredExecutions.some(
        (execution) => execution.id === currentExecutionId
      )
  )
  const shouldValidateDetachedExecution = Boolean(
    currentExecutionId && !currentExecutionInFilteredList
  )
  const {
    execution: detachedExecution,
    executionIsLoading: detachedExecutionIsLoading,
    executionError: detachedExecutionError,
  } = useCompactWorkflowExecution(
    shouldValidateDetachedExecution
      ? (currentExecutionId ?? undefined)
      : undefined
  )
  const executionId = currentExecutionId || filteredExecutions.at(0)?.id

  useEffect(() => {
    if (!shouldValidateDetachedExecution) {
      detachedSelectionIdRef.current = null
      detachedSelectionStartedAtRef.current = null
      return
    }

    if (currentExecutionId !== detachedSelectionIdRef.current) {
      detachedSelectionIdRef.current = currentExecutionId
      detachedSelectionStartedAtRef.current = Date.now()
    }
  }, [shouldValidateDetachedExecution, currentExecutionId])

  // Set up the ref methods
  useEffect(() => {
    if (sidebarRef.current) {
      sidebarRef.current.setActiveTab = setActiveTab
      sidebarRef.current.getActiveTab = () => activeTab
      sidebarRef.current.setOpen = (newOpen: boolean) => {
        setOpen(newOpen)
        if (sidebarRef.current?.collapse && sidebarRef.current?.expand) {
          newOpen ? sidebarRef.current.expand() : sidebarRef.current.collapse()
        }
      }
      sidebarRef.current.isOpen = () => open
    }
  }, [sidebarRef, activeTab, setOpen, open])

  useEffect(() => {
    if (!currentExecutionId || currentExecutionInFilteredList) {
      return
    }
    if (workflowExecutionsIsLoading || detachedExecutionIsLoading) {
      return
    }
    const detached404WithinGrace =
      detachedExecutionError?.status === 404 &&
      detachedSelectionStartedAtRef.current !== null &&
      Date.now() - detachedSelectionStartedAtRef.current <
        DETACHED_EXECUTION_FALLBACK_GRACE_MS
    if (detached404WithinGrace) {
      return
    }
    if (detachedExecution || !detachedExecutionError) {
      return
    }
    setCurrentExecutionId(filteredExecutions.at(0)?.id ?? null)
  }, [
    currentExecutionId,
    currentExecutionInFilteredList,
    workflowExecutionsIsLoading,
    detachedExecutionIsLoading,
    detachedExecution,
    detachedExecutionError,
    filteredExecutions,
    setCurrentExecutionId,
  ])

  if (workflowExecutionsIsLoading) {
    return (
      <div className="flex h-full flex-col items-center justify-center space-y-2">
        <span className="text-xs text-muted-foreground">
          Fetching executions...
        </span>
        <Spinner className="size-6" />
      </div>
    )
  }

  if (workflowExecutionsError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading executions: ${workflowExecutionsError.message}`}
      />
    )
  }

  if (!executionId) {
    return (
      <div className="flex h-full items-center justify-center">
        <Empty className="border-none">
          <EmptyHeader>
            <EmptyMedia variant="icon">
              <WorkflowIcon />
            </EmptyMedia>
            <EmptyTitle>No workflow runs</EmptyTitle>
            <EmptyDescription>
              Get started by running your workflow
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      </div>
    )
  }

  return (
    <BuilderSidebarEventsList
      activeTab={activeTab}
      executionId={executionId}
      executions={filteredExecutions}
      onExecutionChange={setCurrentExecutionId}
    />
  )
}

function BuilderSidebarEventsList({
  activeTab,
  executionId,
  executions,
  onExecutionChange,
}: {
  activeTab: EventsSidebarTabs
  executionId: string
  executions: WorkflowExecutionReadMinimal[]
  onExecutionChange: (executionId: string) => void
}) {
  const { appSettings } = useOrgAppSettings()
  const { sidebarRef } = useWorkflowBuilder()
  const selectedExecution = executions.find(
    (execution) => execution.id === executionId
  )

  const { execution, executionIsLoading, executionError } =
    useCompactWorkflowExecution(executionId)

  console.debug({
    execId: execution?.id,
    execIsLoading: executionIsLoading,
    execError: executionError,
  })

  if (executionIsLoading) {
    return (
      <div className="flex h-full flex-col items-center justify-center space-y-2">
        <span className="text-xs text-muted-foreground">
          Fetching events...
        </span>
        <Spinner className="size-6" />
      </div>
    )
  }
  if (executionError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading execution: ${executionError.message}`}
      />
    )
  }
  if (!execution) {
    return (
      <EventsSidebarEmpty
        title="Could not find execution"
        description="Please refresh the page and try again"
      />
    )
  }
  const tabItems = [
    {
      value: "workflow-events",
      label: "Events",
      icon: CalendarSearchIcon,
      content: (
        <>
          <WorkflowEventsHeader execution={execution} />
          {appSettings?.app_interactions_enabled && (
            <WorkflowInteractions execution={execution} />
          )}
          <WorkflowEvents events={execution.events} status={execution.status} />
        </>
      ),
    },
    {
      value: "action-input",
      label: "Input",
      icon: FileInputIcon,
      content: <ActionEventPane execution={execution} type="input" />,
    },
    {
      value: "action-result",
      label: "Result",
      icon: ShapesIcon,
      content: <ActionEventPane execution={execution} type="result" />,
    },
  ]
  if (appSettings?.app_interactions_enabled) {
    tabItems.push({
      value: "action-interaction",
      label: "Interaction",
      icon: MessagesSquare,
      content: <ActionEventPane execution={execution} type="interaction" />,
    })
  }

  return (
    <div className="h-full">
      <Tabs
        value={activeTab}
        onValueChange={(value: string) => {
          if (sidebarRef.current?.setActiveTab) {
            sidebarRef.current.setActiveTab(value as EventsSidebarTabs)
          }
        }}
        className="flex size-full flex-col"
      >
        <div className="p-2">
          <Select value={executionId} onValueChange={onExecutionChange}>
            <SelectTrigger className="h-9 px-2.5">
              <div className="flex min-w-0 flex-1 items-center gap-2 overflow-hidden">
                {selectedExecution ? (
                  <ExecutionOptionRow execution={selectedExecution} />
                ) : (
                  <div className="flex min-w-0 items-center gap-2">
                    <TimerResetIcon className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate font-mono text-xs">
                      {executionId}
                    </span>
                    <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                      Checked out
                    </span>
                  </div>
                )}
              </div>
            </SelectTrigger>
            <SelectContent>
              {!selectedExecution && (
                <SelectItem value={executionId} className="py-1.5">
                  <div className="flex min-w-0 items-center gap-2 overflow-hidden">
                    <TimerResetIcon className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate font-mono text-xs">
                      {executionId}
                    </span>
                    <span className="shrink-0 rounded bg-muted px-1.5 py-0.5 text-[10px] text-muted-foreground">
                      Checked out
                    </span>
                  </div>
                </SelectItem>
              )}
              {executions.map((execution) => (
                <SelectItem
                  key={execution.id}
                  value={execution.id}
                  className="py-1.5"
                >
                  <ExecutionOptionRow execution={execution} />
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
        <div className="sticky top-0 z-10 mt-0.5 bg-background">
          <ScrollArea className="w-full whitespace-nowrap rounded-md">
            <TabsList className="inline-flex h-8 flex-1 items-center justify-start bg-transparent p-0">
              {tabItems.map((tab) => (
                <TabsTrigger
                  key={tab.value}
                  value={tab.value}
                  className="flex h-full min-w-20 items-center justify-center rounded-none py-0 text-xs data-[state=active]:bg-transparent data-[state=active]:shadow-none sm:min-w-16 md:min-w-20"
                >
                  <tab.icon className="mr-2 size-4 sm:mr-1" />
                  <span className="hidden sm:inline">{tab.label}</span>
                  <span className="sm:hidden">{tab.label.slice(0, 4)}</span>
                </TabsTrigger>
              ))}
            </TabsList>
            <ScrollBar orientation="horizontal" className="invisible" />
          </ScrollArea>
        </div>
        <Separator />
        <div className="size-full overflow-scroll">
          {tabItems.map((tab) => (
            <TabsContent
              key={tab.value}
              value={tab.value}
              className="m-0 size-full min-w-[200px] p-0"
            >
              {tab.content}
            </TabsContent>
          ))}
        </div>
      </Tabs>
    </div>
  )
}
