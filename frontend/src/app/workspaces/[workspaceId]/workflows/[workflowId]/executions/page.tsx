"use client"

import React from "react"
import { EventHistoryResponse } from "@/client"
import { useWorkflow } from "@/providers/workflow"
import { ImperativePanelHandle } from "react-resizable-panels"

import { useWorkflowExecutions } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { ScrollArea } from "@/components/ui/scroll-area"
import { TooltipProvider } from "@/components/ui/tooltip"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"

import "react18-json-view/src/style.css"

import { WorkflowExecutionEventDetailView } from "@/components/executions/event-details"
import { WorkflowExecutionEventHistory } from "@/components/executions/event-history"
import { WorkflowExecutionNav } from "@/components/executions/nav"

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
          minSize={15}
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
              setSelectedEvent={setSelectedEvent}
            />
          </ScrollArea>
        </ResizablePanel>
        <ResizableHandle withHandle />

        {/* For items that should align at the end of the side nav */}
        <ResizablePanel
          defaultSize={defaultLayout[1]}
          minSize={15}
          className={cn("flex h-full flex-col p-2", isCollapsed && "min-w-14")}
        >
          <ScrollArea className="overflow-auto">
            <SectionHead text="Event History" />
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
          </ScrollArea>
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel defaultSize={defaultLayout[2]} minSize={25}>
          <ScrollArea className="overflow-auto">
            {selectedEvent ? (
              <WorkflowExecutionEventDetailView event={selectedEvent} />
            ) : (
              <span className="flex justify-center p-4 text-center text-xs text-muted-foreground">
                Select an Event.
              </span>
            )}
          </ScrollArea>
        </ResizablePanel>
      </ResizablePanelGroup>
    </TooltipProvider>
  )
}

function SectionHead({ text }: { text: string }) {
  return (
    <span className="flex w-full justify-start px-2 py-1 text-center text-xs font-normal text-muted-foreground/80">
      <Badge variant="secondary">{text}</Badge>
    </span>
  )
}
