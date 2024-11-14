"use client"

import React from "react"
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

import { ListVideoIcon } from "lucide-react"

import { WorkflowExecutionNav } from "@/components/executions/nav"
import { SectionHead } from "@/components/executions/section"

export default function WorkflowExecutionsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const { workflowId } = useWorkflow()
  return (
    <div className="h-[calc(100vh-4rem)]">
      <div className="h-full">
        {workflowId ? (
          <WorkflowExecutionsPanelGroup
            workflowId={workflowId}
            navCollapsedSize={10}
          >
            {children}
          </WorkflowExecutionsPanelGroup>
        ) : (
          <CenteredSpinner />
        )}
      </div>
    </div>
  )
}

function WorkflowExecutionsPanelGroup({
  workflowId,
  defaultLayout = [15, 15, 70],
  defaultCollapsed = false,
  navCollapsedSize,
  children,
}: {
  workflowId: string
  defaultLayout?: number[]
  defaultCollapsed?: boolean
  navCollapsedSize: number
  children: React.ReactNode
}) {
  const sidePanelRef = React.useRef<ImperativePanelHandle>(null)
  const [isCollapsed, setIsCollapsed] = React.useState(defaultCollapsed)
  const {
    workflowExecutions,
    workflowExecutionsError,
    workflowExecutionsIsLoading,
  } = useWorkflowExecutions(workflowId)

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
              <WorkflowExecutionNav executions={workflowExecutions} />
            </div>
          </div>
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel
          defaultSize={defaultLayout[1] + defaultLayout[2]}
          minSize={15}
          className={cn(isCollapsed && "min-w-14")}
        >
          {children}
        </ResizablePanel>
      </ResizablePanelGroup>
    </TooltipProvider>
  )
}
