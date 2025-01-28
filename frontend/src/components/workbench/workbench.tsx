"use client"

import * as React from "react"
import { useWorkflowBuilder } from "@/providers/builder"
import { SidebarIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  CustomResizableHandle,
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { WorkflowCanvas } from "@/components/workbench/canvas/canvas"
import { WorkbenchSidebarEvents } from "@/components/workbench/events/events-sidebar"
import { WorkbenchPanel } from "@/components/workbench/panel/workbench-panel"

interface WorkbenchProps {
  defaultLayout: number[] | undefined
  defaultCollapsed?: boolean
}

export function Workbench({ defaultLayout = [0, 68, 32] }: WorkbenchProps) {
  const { canvasRef, sidebarRef, isSidebarCollapsed, toggleSidebar } =
    useWorkflowBuilder()
  return (
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
        <ResizablePanel
          ref={sidebarRef}
          defaultSize={defaultLayout[0]}
          collapsedSize={0}
          collapsible={true}
          minSize={20}
          maxSize={40}
          className="h-full"
        >
          <WorkbenchSidebarEvents />
        </ResizablePanel>
        <TooltipProvider>
          <Tooltip>
            <CustomResizableHandle>
              <TooltipTrigger asChild>
                <Button
                  variant="ghost"
                  className={cn(
                    "absolute top-0 m-0 translate-x-6 rounded-full !bg-transparent p-4 active:cursor-grabbing"
                  )}
                  onClick={toggleSidebar}
                >
                  <div className="group rounded-sm p-1 hover:bg-border">
                    <SidebarIcon className="group size-4 text-muted-foreground group-hover:text-foreground" />
                  </div>
                </Button>
              </TooltipTrigger>
              {isSidebarCollapsed && (
                <TooltipContent side="right">View Events</TooltipContent>
              )}
            </CustomResizableHandle>
          </Tooltip>
        </TooltipProvider>
        <ResizablePanel defaultSize={defaultLayout[1]}>
          <WorkflowCanvas ref={canvasRef} />
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel defaultSize={defaultLayout[2]} minSize={32}>
          <WorkbenchPanel />
        </ResizablePanel>
      </ResizablePanelGroup>
    </TooltipProvider>
  )
}
