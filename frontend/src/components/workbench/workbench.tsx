"use client"

import * as React from "react"
import { WorkflowBuilderProvider } from "@/providers/builder"
import { ReactFlowProvider } from "reactflow"

import {
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { TooltipProvider } from "@/components/ui/tooltip"
import { WorkflowCanvas } from "@/components/workbench/canvas/canvas"
import { WorkspacePanel } from "@/components/workbench/panel/workbench-panel"

interface WorkbenchProps {
  defaultLayout: number[] | undefined
  defaultCollapsed?: boolean
}

export function Workbench({
  defaultLayout = [1, 60, 20],
}: WorkbenchProps) {

  return (
    <ReactFlowProvider>
      <WorkflowBuilderProvider>
        <TooltipProvider delayDuration={0}>
          <ResizablePanelGroup
            className="h-full"
            direction="horizontal"
            onLayout={(sizes: number[]) => {
              document.cookie = `react-resizable-panels:layout=${JSON.stringify(
                sizes
              )}`
            }}
          >
            <ResizablePanel defaultSize={defaultLayout[1]}>
              <WorkflowCanvas />
            </ResizablePanel>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize={defaultLayout[2]} minSize={30}>
              <WorkspacePanel />
            </ResizablePanel>
          </ResizablePanelGroup>
        </TooltipProvider>
      </WorkflowBuilderProvider>
    </ReactFlowProvider>
  )
}
