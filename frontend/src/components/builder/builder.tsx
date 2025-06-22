"use client"

import { SidebarIcon } from "lucide-react"
import * as React from "react"
import { WorkflowCanvas } from "@/components/builder/canvas/canvas"
import { BuilderSidebarEvents } from "@/components/builder/events/events-sidebar"
import { EventsSidebarToolbar } from "@/components/builder/events/events-sidebar-toolbar"
import { BuilderPanel } from "@/components/builder/panel/builder-panel"
import { WorkflowBuilderErrorBoundary } from "@/components/error-boundaries"
import { Button } from "@/components/ui/button"
import {
  CustomResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"
import { useWorkflowBuilder } from "@/providers/builder"

interface BuilderProps {
  defaultLayout: number[] | undefined
  defaultCollapsed?: boolean
}

export function Builder({ defaultLayout = [0, 68, 24] }: BuilderProps) {
  const {
    canvasRef,
    sidebarRef,
    isSidebarCollapsed,
    toggleSidebar,
    actionPanelRef,
    isActionPanelCollapsed,
    toggleActionPanel,
  } = useWorkflowBuilder()

  // Add keyboard shortcut for toggling sidebar (Cmd+E)
  React.useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      // Check for Cmd+E (or Ctrl+E for non-Mac)
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "e") {
        event.preventDefault() // Prevent default browser behavior
        toggleSidebar()
      }
    }

    // Add event listener
    window.addEventListener("keydown", handleKeyDown)

    // Clean up on component unmount
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [toggleSidebar])

  // Add keyboard shortcut for toggling action panel (Cmd+Shift+E or Ctrl+Shift+E)
  React.useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "u") {
        event.preventDefault() // Prevent default browser behavior
        toggleActionPanel()
      }
    }

    // Add event listener
    window.addEventListener("keydown", handleKeyDown)

    // Clean up on component unmount
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [toggleActionPanel])

  return (
    <WorkflowBuilderErrorBoundary>
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
            minSize={24}
            maxSize={38}
            className="relative h-full"
          >
            <BuilderSidebarEvents />
            <EventsSidebarToolbar />
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
          <TooltipProvider>
            <Tooltip>
              <CustomResizableHandle>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    className={cn(
                      "absolute top-0 m-0 -translate-x-6 rounded-full !bg-transparent p-4 active:cursor-grabbing"
                    )}
                    onClick={toggleActionPanel}
                  >
                    <div className="group rounded-sm p-1 hover:bg-border">
                      <SidebarIcon className="group size-4 -scale-x-100 text-muted-foreground group-hover:text-foreground" />
                    </div>
                  </Button>
                </TooltipTrigger>
                {isActionPanelCollapsed && (
                  <TooltipContent side="right">View Side Panel</TooltipContent>
                )}
              </CustomResizableHandle>
            </Tooltip>
          </TooltipProvider>
          <ResizablePanel
            ref={actionPanelRef}
            defaultSize={defaultLayout[2]}
            collapsedSize={0}
            collapsible={true}
            minSize={3}
            maxSize={58}
            className="h-full"
          >
            <BuilderPanel ref={actionPanelRef} />
          </ResizablePanel>
        </ResizablePanelGroup>
      </TooltipProvider>
    </WorkflowBuilderErrorBoundary>
  )
}
