"use client"

import { useEffect, useState } from "react"
import { WorkflowExecutionReadMinimal } from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"
import { CalendarSearchIcon, FileInputIcon, ShapesIcon } from "lucide-react"
import { ImperativePanelHandle } from "react-resizable-panels"

import {
  useCompactWorkflowExecution,
  useManualWorkflowExecution,
} from "@/lib/hooks"
import { ScrollArea, ScrollBar } from "@/components/ui/scroll-area"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { ActionEvent } from "@/components/workbench/events/events-selected-action"
import { EventsSidebarEmpty } from "@/components/workbench/events/events-sidebar-empty"
import {
  WorkflowEvents,
  WorkflowEventsHeader,
} from "@/components/workbench/events/events-workflow"

export type EventsSidebarTabs = "workflow-events" | "action-event"
/**
 * Interface for controlling the events sidebar through a ref
 */
export interface EventsSidebarRef extends ImperativePanelHandle {
  /** Sets the active tab in the events sidebar */
  setActiveTab: (tab: EventsSidebarTabs) => void
  /** Gets the current active tab */
  getActiveTab: () => EventsSidebarTabs
}

export function WorkbenchSidebarEvents() {
  const { sidebarRef } = useWorkflowBuilder()
  const [activeTab, setActiveTab] =
    useState<EventsSidebarTabs>("workflow-events")
  const { workflow, isLoading } = useWorkflow()

  // Set up the ref methods
  useEffect(() => {
    if (sidebarRef.current) {
      sidebarRef.current.setActiveTab = setActiveTab
      sidebarRef.current.getActiveTab = () => activeTab
    }
  }, [sidebarRef, activeTab])

  if (isLoading) return <CenteredSpinner />
  if (!workflow)
    return (
      <div className="flex items-center justify-center text-muted-foreground">
        No workflow in context
      </div>
    )
  return (
    <WorkbenchSidebarEventsTable
      workflowId={workflow.id}
      activeTab={activeTab}
    />
  )
}

function WorkbenchSidebarEventsTable({
  workflowId,
  activeTab,
}: {
  workflowId: string
  activeTab: EventsSidebarTabs
}) {
  const [status, setStatus] = useState<
    WorkflowExecutionReadMinimal["status"] | undefined
  >(undefined)
  const refetchInterval = status === "RUNNING" ? REFETCH_INTERVAL : undefined
  const { lastExecution } = useManualWorkflowExecution(workflowId, {
    refetchInterval,
  })
  useEffect(() => {
    if (lastExecution) {
      setStatus(lastExecution.status)
    }
  }, [lastExecution])
  const currId = lastExecution?.id
  return currId ? (
    <WorkbenchSidebarEventsList
      executionId={currId}
      status={status}
      activeTab={activeTab}
    />
  ) : (
    <EventsSidebarEmpty
      title="No workflow runs"
      description="Get started by running your workflow"
      actionLabel="New workflow"
    />
  )
}
const REFETCH_INTERVAL = 500

function WorkbenchSidebarEventsList({
  executionId,
  status,
  activeTab,
}: {
  executionId: string
  status?: WorkflowExecutionReadMinimal["status"]
  activeTab: EventsSidebarTabs
}) {
  const { sidebarRef } = useWorkflowBuilder()
  const refetchInterval = status === "RUNNING" ? REFETCH_INTERVAL : undefined
  const escapedExecutionId = encodeURIComponent(executionId)
  const { execution, executionIsLoading, executionError } =
    useCompactWorkflowExecution(escapedExecutionId, {
      refetchInterval,
    })
  if (executionIsLoading) return <CenteredSpinner />
  if (executionError || !execution)
    return (
      <AlertNotification
        level="error"
        message={`Error loading execution: ${executionError?.message || "Execution undefined"}`}
      />
    )

  const tabItems = [
    {
      value: "workflow-events",
      label: "Events",
      icon: CalendarSearchIcon,
      content: (
        <>
          <WorkflowEventsHeader execution={execution} />
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
        <div className="sticky top-0 z-10 mt-2 bg-background">
          <ScrollArea className="w-full whitespace-nowrap rounded-md">
            <TabsList className="inline-flex h-8 w-full items-center justify-start bg-transparent p-0">
              {tabItems.map((tab) => (
                <TabsTrigger
                  key={tab.value}
                  value={tab.value}
                  className="flex h-full min-w-28 items-center justify-center rounded-none border-b-2 border-transparent py-0 text-xs data-[state=active]:border-primary data-[state=active]:bg-transparent data-[state=active]:shadow-none"
                >
                  <tab.icon className="mr-2 size-4" />
                  <span>{tab.label}</span>
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
