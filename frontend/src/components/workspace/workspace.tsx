"use client"

import * as React from "react"
import { WorkflowBuilderProvider } from "@/providers/builder"
import { ChevronsLeft, ChevronsRight } from "lucide-react"
import { ImperativePanelHandle } from "react-resizable-panels"
import { ReactFlowProvider } from "reactflow"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  CustomResizableHandle,
  ResizableHandle,
  ResizablePanel,
  ResizablePanelGroup,
} from "@/components/ui/resizable"
import { Separator } from "@/components/ui/separator"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { TooltipProvider } from "@/components/ui/tooltip"
import { WorkflowCanvas } from "@/components/workspace/canvas/canvas"
import { ActionCatalog } from "@/components/workspace/catalog/action-catalog"
import { actionTiles } from "@/components/workspace/catalog/action-tiles-schema"
import IntegrationsCatalog from "@/components/workspace/catalog/integrations-catalog"
import { WorkspacePanel } from "@/components/workspace/panel/workspace-panel"

interface WorkspaceProps {
  defaultLayout: number[] | undefined
  defaultCollapsed?: boolean
  navCollapsedSize: number
}

export function Workspace({
  defaultLayout = [1, 60, 20],
  defaultCollapsed = false,
  navCollapsedSize,
}: WorkspaceProps) {
  const sidePanelRef = React.useRef<ImperativePanelHandle>(null)
  const [isCollapsed, setIsCollapsed] = React.useState(defaultCollapsed)

  // Adjust onCollapse to match the expected signature
  const handleCollapse = () => {
    // Assuming you have a way to set the collapsed state here
    setIsCollapsed(true) // Set to true when you know the panel is collapsed
    document.cookie = `react-resizable-panels:collapsed=${JSON.stringify(true)}`
  }

  // Adjust onExpand to match the expected signature
  const handleExpand = () => {
    // Assuming you have a way to set the collapsed state here
    setIsCollapsed(false) // Set to false when you know the panel is expanded
    document.cookie = `react-resizable-panels:collapsed=${JSON.stringify(false)}`
  }
  const toggleSidePanel = () => {
    const side = sidePanelRef.current
    if (!side) {
      return
    }
    if (side.isCollapsed()) {
      side.expand()
    } else {
      side.collapse()
    }
    setIsCollapsed(!isCollapsed)
    document.cookie = `react-resizable-panels:collapsed=${JSON.stringify(
      !isCollapsed
    )}`
  }

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
            <ResizablePanel
              ref={sidePanelRef}
              defaultSize={defaultLayout[0]}
              collapsedSize={navCollapsedSize}
              collapsible={true}
              minSize={12}
              maxSize={20}
              onCollapse={handleCollapse}
              onExpand={handleExpand}
              className={cn(
                "flex h-full flex-col p-2",
                isCollapsed &&
                  "min-w-[50px] transition-all duration-300 ease-in-out"
              )}
            >
              <CatalogTabs isCollapsed={isCollapsed} />
              {/* For items that should align at the end of the side nav */}
            </ResizablePanel>
            <CustomResizableHandle>
              <Button
                variant="ghost"
                className="rounded-full shadow-sm hover:bg-transparent"
                onClick={toggleSidePanel}
              >
                {isCollapsed ? (
                  <ChevronsRight className="h-3 w-3" />
                ) : (
                  <ChevronsLeft className="h-3 w-3" />
                )}
              </Button>
            </CustomResizableHandle>
            <ResizablePanel defaultSize={defaultLayout[1]}>
              <WorkflowCanvas />
            </ResizablePanel>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize={defaultLayout[2]} minSize={25}>
              <WorkspacePanel />
            </ResizablePanel>
          </ResizablePanelGroup>
        </TooltipProvider>
      </WorkflowBuilderProvider>
    </ReactFlowProvider>
  )
}

export function CatalogTabs({ isCollapsed }: { isCollapsed: boolean }) {
  return (
    <Tabs
      defaultValue="actions"
      className="w-full"
      value={isCollapsed ? "actions" : undefined}
    >
      {!isCollapsed && (
        <>
          <TabsList className="grid w-full grid-cols-2 bg-transparent hover:cursor-pointer">
            <TabsTrigger
              value="actions"
              className="text-xs data-[state=active]:shadow-none"
              asChild
            >
              <span>Actions</span>
            </TabsTrigger>
            <TabsTrigger
              value="integrations"
              className="text-xs data-[state=active]:shadow-none"
              asChild
            >
              <span>Integrations</span>
            </TabsTrigger>
          </TabsList>
          <Separator />
        </>
      )}
      <TabsContent value="actions">
        <ActionCatalog isCollapsed={isCollapsed} tiles={actionTiles} />
      </TabsContent>
      <TabsContent value="integrations">
        <IntegrationsCatalog />
      </TabsContent>
    </Tabs>
  )
}
