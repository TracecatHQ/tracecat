"use client"

import { PanelLeft } from "lucide-react"
import * as React from "react"
import { WorkflowCanvas } from "@/components/builder/canvas/canvas"
import { BuilderSidebarEvents } from "@/components/builder/events/events-sidebar"
import { EventsSidebarToolbar } from "@/components/builder/events/events-sidebar-toolbar"
import { BuilderPanel } from "@/components/builder/panel/builder-panel"
import { WorkflowBuilderErrorBoundary } from "@/components/error-boundaries"
import { Button } from "@/components/ui/button"
import { Kbd } from "@/components/ui/kbd"
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
import { parseShortcutKeys } from "@/lib/tiptap-utils"
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
    toggleSidebar,
    actionPanelRef,
    toggleActionPanel,
  } = useWorkflowBuilder()
  const toggleSidebarShortcut = React.useMemo(
    () => parseShortcutKeys({ shortcutKeys: "mod+e" }),
    []
  )
  const toggleActionPanelShortcut = React.useMemo(
    () => parseShortcutKeys({ shortcutKeys: "mod+shift+e" }),
    []
  )

  // Toggle builder panels with Cmd/Ctrl+E (left) and Cmd/Ctrl+Shift+E (right)
  React.useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => {
      if (!(event.metaKey || event.ctrlKey) || event.repeat) {
        return
      }
      const key = event.key.toLowerCase()
      if (key === "e") {
        event.preventDefault()
        if (event.shiftKey) {
          toggleActionPanel()
          return
        }
        toggleSidebar()
      }
    }

    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [toggleActionPanel, toggleSidebar])

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
                      <PanelLeft className="group size-4 text-muted-foreground group-hover:text-foreground" />
                    </div>
                  </Button>
                </TooltipTrigger>
                <TooltipContent
                  side="right"
                  className="border-0 bg-transparent p-0 shadow-none"
                >
                  <span className="inline-flex items-center gap-1">
                    {toggleSidebarShortcut.map((key) => (
                      <Kbd key={key}>{key}</Kbd>
                    ))}
                  </span>
                </TooltipContent>
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
                      <PanelLeft className="group size-4 -scale-x-100 text-muted-foreground group-hover:text-foreground" />
                    </div>
                  </Button>
                </TooltipTrigger>
                <TooltipContent
                  side="left"
                  className="border-0 bg-transparent p-0 shadow-none"
                >
                  <span className="inline-flex items-center gap-1">
                    {toggleActionPanelShortcut.map((key) => (
                      <Kbd key={key}>{key}</Kbd>
                    ))}
                  </span>
                </TooltipContent>
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
