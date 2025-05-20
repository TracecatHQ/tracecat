"use client"

import { useEffect, useState } from "react"
import { $TriggerType, TriggerType } from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"
import {
  CalendarSearchIcon,
  FileInputIcon,
  MessagesSquare,
  ShapesIcon,
} from "lucide-react"
import { ImperativePanelHandle } from "react-resizable-panels"

import {
  useCompactWorkflowExecution,
  useLastExecution,
  useLocalStorage,
  useOrgAppSettings,
} from "@/lib/hooks"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { ActionEvent } from "@/components/builder/events/events-selected-action"
import { EventsSidebarEmpty } from "@/components/builder/events/events-sidebar-empty"
import { WorkflowInteractions } from "@/components/builder/events/events-sidebar-interactions"
import {
  WorkflowEvents,
  WorkflowEventsHeader,
} from "@/components/builder/events/events-workflow"
import { Spinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"

// Define the available trigger types for UI generation
const AVAILABLE_TRIGGER_TYPES: readonly TriggerType[] = $TriggerType.enum

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
  const { sidebarRef, currentExecutionId } = useWorkflowBuilder()
  const [activeTab, setActiveTab] =
    useState<EventsSidebarTabs>("workflow-events")
  const [open, setOpen] = useState(false)
  const [selectedTriggerTypes, setSelectedTriggerTypes] = useLocalStorage<
    TriggerType[]
  >("selected-trigger-types", [...AVAILABLE_TRIGGER_TYPES])

  const { lastExecution, lastExecutionIsLoading, lastExecutionError } =
    useLastExecution({
      workflowId: currentExecutionId ? undefined : workflowId,
      triggerTypes: selectedTriggerTypes,
    })

  // Determine which execution ID to use
  // Prefer currentExecutionId (from direct trigger) over lastExecution.id (from query)
  const executionId = currentExecutionId || lastExecution?.id

  console.debug({
    currentExecId: currentExecutionId,
    lastExecId: lastExecution?.id,
    executionIdUsed: executionId,
    lastExecIsLoading: lastExecutionIsLoading,
    lastExecError: lastExecutionError,
    selectedTriggers: selectedTriggerTypes,
  })

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

  // If we have a direct execution ID, we can skip the loading state for last execution
  if (!currentExecutionId && lastExecutionIsLoading) {
    return (
      <div className="flex h-full flex-col items-center justify-center space-y-2">
        <span className="text-xs text-muted-foreground">
          Fetching last execution...
        </span>
        <Spinner className="size-6" />
      </div>
    )
  }

  if (!currentExecutionId && lastExecutionError) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading last execution: ${lastExecutionError.message}`}
      />
    )
  }

  // If we have no execution ID (neither current nor last), show the empty state
  if (!executionId) {
    return (
      <EventsSidebarEmpty
        title="No workflow runs"
        description="Get started by running your workflow"
        actionLabel="New workflow"
      />
    )
  }

  // We have an execution ID to use
  return (
    <BuilderSidebarEventsList activeTab={activeTab} executionId={executionId} />
  )
}

function BuilderSidebarEventsList({
  activeTab,
  executionId,
}: {
  activeTab: EventsSidebarTabs
  executionId: string
}) {
  const { appSettings } = useOrgAppSettings()
  const { sidebarRef } = useWorkflowBuilder()

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
          <WorkflowEvents events={execution.events} />
        </>
      ),
    },
    {
      value: "action-input",
      label: "Input",
      icon: FileInputIcon,
      content: <ActionEvent execution={execution} type="input" />,
    },
    {
      value: "action-result",
      label: "Result",
      icon: ShapesIcon,
      content: <ActionEvent execution={execution} type="result" />,
    },
  ]
  if (appSettings?.app_interactions_enabled) {
    tabItems.push({
      value: "action-interaction",
      label: "Interaction",
      icon: MessagesSquare,
      content: <ActionEvent execution={execution} type="interaction" />,
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
        <div className="sticky top-0 z-10 mt-0.5 bg-background">
          <ScrollArea className="w-full whitespace-nowrap rounded-md">
            <TabsList className="inline-flex h-8 w-full items-center justify-start bg-transparent p-0">
              {tabItems.map((tab) => (
                <TabsTrigger
                  key={tab.value}
                  value={tab.value}
                  className="flex h-full min-w-20 items-center justify-center rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none sm:min-w-16 md:min-w-20"
                >
                  {/* TODO(chris): Please adjust this */}
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
        <ScrollArea className="!m-0 flex-1 rounded-md p-0">
          <div className="overflow-y-auto">
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
          <ScrollBar orientation="horizontal" />
          <ScrollBar orientation="vertical" />
        </ScrollArea>
      </Tabs>
    </div>
  )
}
