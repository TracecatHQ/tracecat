"use client"

import { useEffect, useState } from "react"
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
  useManualWorkflowExecution,
  useOrgAppSettings,
} from "@/lib/hooks"
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { ActionEvent } from "@/components/workbench/events/events-selected-action"
import { EventsSidebarEmpty } from "@/components/workbench/events/events-sidebar-empty"
import { WorkflowInteractions } from "@/components/workbench/events/events-sidebar-interactions"
import {
  WorkflowEvents,
  WorkflowEventsHeader,
} from "@/components/workbench/events/events-workflow"

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

export function WorkbenchSidebarEvents() {
  const { sidebarRef } = useWorkflowBuilder()
  const [activeTab, setActiveTab] =
    useState<EventsSidebarTabs>("workflow-events")
  const [open, setOpen] = useState(false)
  const { workflow, isLoading } = useWorkflow()

  // Set up the ref methods
  useEffect(() => {
    if (sidebarRef.current) {
      sidebarRef.current.setActiveTab = setActiveTab
      sidebarRef.current.getActiveTab = () => activeTab
      sidebarRef.current.setOpen = (newOpen: boolean) => {
        setOpen(newOpen)
        // If the panel has a collapse method, use it
        if (sidebarRef.current?.collapse && sidebarRef.current?.expand) {
          newOpen ? sidebarRef.current.expand() : sidebarRef.current.collapse()
        }
      }
      sidebarRef.current.isOpen = () => open
    }
  }, [sidebarRef, activeTab, setOpen, open])

  if (isLoading) return <CenteredSpinner />
  if (!workflow)
    return (
      <div className="flex items-center justify-center text-muted-foreground">
        No workflow in context
      </div>
    )

  return (
    <WorkbenchSidebarEventsList
      workflowId={workflow.id}
      activeTab={activeTab}
    />
  )
}

function WorkbenchSidebarEventsList({
  workflowId,
  activeTab,
}: {
  workflowId: string
  activeTab: EventsSidebarTabs
}) {
  const { appSettings } = useOrgAppSettings()
  const { sidebarRef } = useWorkflowBuilder()
  const { lastExecution, lastExecutionIsLoading, lastExecutionError } =
    useManualWorkflowExecution(workflowId)

  // Pre-fetch and create query observer even if we don't have an ID yet
  const { execution, executionIsLoading, executionError } =
    useCompactWorkflowExecution(lastExecution?.id)

  // Show loading state if either query is loading
  if (lastExecutionIsLoading || (lastExecution?.id && executionIsLoading)) {
    return <CenteredSpinner />
  }

  // Only show error if we have a real error (not just missing execution)
  if (lastExecutionError || (lastExecution?.id && executionError)) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading execution: ${
          lastExecutionError?.message ||
          executionError?.message ||
          "Error loading last execution"
        }`}
      />
    )
  }

  // Show empty state if we have no execution data
  if (!lastExecution || !execution) {
    return (
      <EventsSidebarEmpty
        title="No workflow runs"
        description="Get started by running your workflow"
        actionLabel="New workflow"
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
