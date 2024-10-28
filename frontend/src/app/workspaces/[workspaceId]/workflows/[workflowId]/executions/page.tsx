"use client"

import React from "react"
import { EventHistoryResponse } from "@/client"
import { useWorkflow } from "@/providers/workflow"
import { ImperativePanelHandle } from "react-resizable-panels"

import { useWorkflowExecutions } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { TooltipProvider } from "@/components/ui/tooltip"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"

import "react18-json-view/src/style.css"

import { History, ListVideoIcon } from "lucide-react"

import { WorkflowExecutionEventDetailView } from "@/components/executions/event-details"
import { WorkflowExecutionEventHistory } from "@/components/executions/event-history"
import { WorkflowExecutionNav } from "@/components/executions/nav"

export default function WorkflowExecutionsPage() {
  const { workflowId } = useWorkflow()
  return (
    <div className="h-[calc(100vh-4rem)]">
      <div className="h-full">
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

function WorkflowExecutionsViewLayout({
  workflowId,
  defaultLayout = [15, 15, 70],
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
  const [selectedEvent, setSelectedEvent] = React.useState<
    EventHistoryResponse | undefined
  >()

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
        direction="horizontal"
        className="h-full"
        onLayout={(sizes: number[]) => {
          document.cookie = `workflow-executions:react-resizable-panels:layout=${JSON.stringify(
            sizes
          )}`
        }}
      >
        {/* Panel 1: All executions */}
        <ResizablePanel
          ref={sidePanelRef}
          defaultSize={defaultLayout[0]}
          collapsedSize={navCollapsedSize}
          collapsible={true}
          minSize={15}
          maxSize={20}
          onCollapse={handleCollapse}
          onExpand={handleExpand}
          className={cn(isCollapsed && "min-w-14")}
        >
          <div className="flex h-full flex-col overflow-hidden p-2">
            <div className="flex-none">
              <SectionHead
                text="Workflow Runs"
                icon={<ListVideoIcon className="mr-2 size-4" strokeWidth={2} />}
              />
            </div>
            <div className="flex-1 overflow-auto">
              <WorkflowExecutionNav
                executions={workflowExecutions}
                executionId={executionId}
                setExecutionId={setExecutionId}
                setSelectedEvent={setSelectedEvent}
              />
            </div>
          </div>
        </ResizablePanel>
        <ResizableHandle withHandle />

        {/* Panel 2: Event History */}
        <ResizablePanel
          defaultSize={defaultLayout[1]}
          minSize={15}
          className={cn(isCollapsed && "min-w-14")}
        >
          <div className="flex h-full flex-col overflow-hidden p-2">
            <div className="flex-none">
              <SectionHead
                text="Event History"
                icon={<History className="mr-2 size-4" strokeWidth={2} />}
              />
            </div>
            <div className="flex-1 overflow-auto">
              {executionId ? (
                <WorkflowExecutionEventHistory
                  executionId={executionId}
                  selectedEvent={selectedEvent}
                  setSelectedEvent={setSelectedEvent}
                />
              ) : (
                <span className="flex justify-center p-4 text-center text-xs text-muted-foreground">
                  Select a Workflow Execution.
                </span>
              )}
            </div>
          </div>
        </ResizablePanel>
        <ResizableHandle withHandle />

        {/* Panel 3: Event Details */}
        <ResizablePanel
          defaultSize={defaultLayout[2]}
          minSize={25}
          className="grow"
        >
          <div className="flex h-full flex-col overflow-hidden">
            <div className="flex-1 overflow-auto">
              {selectedEvent ? (
                <WorkflowExecutionEventDetailView event={selectedEvent} />
              ) : (
                <span className="flex justify-center p-4 text-center text-xs text-muted-foreground">
                  Select an Event.
                </span>
              )}
            </div>
          </div>
        </ResizablePanel>
      </ResizablePanelGroup>
    </TooltipProvider>
  )
}

function SectionHead({ icon, text }: { icon: React.ReactNode; text: string }) {
  return (
    <div className="flex w-full justify-start p-2 text-center text-xs font-semibold">
      {icon}
      <span>{text}</span>
    </div>
  )
}
