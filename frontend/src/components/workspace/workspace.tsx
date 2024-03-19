"use client"

import * as React from "react"
import { WorkflowBuilderProvider } from "@/providers/builder"
import {
  Blend,
  BookText,
  CheckSquare,
  ChevronsLeft,
  ChevronsRight,
  Container,
  FlaskConical,
  GitCompareArrows,
  Globe,
  Languages,
  Mail,
  Regex,
  Send,
  ShieldAlert,
  Sparkles,
  Split,
  Tags,
  Webhook,
} from "lucide-react"
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
import { TooltipProvider } from "@/components/ui/tooltip"
import { ActionTiles } from "@/components/workspace/action-tiles"
import { WorkflowCanvas } from "@/components/workspace/canvas"
import { WorkspacePanel } from "@/components/workspace/workspace-panel"

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
                isCollapsed &&
                  "min-w-[50px] transition-all duration-300 ease-in-out"
              )}
            >
              <ActionTiles
                isCollapsed={isCollapsed}
                tiles={[
                  {
                    type: "webhook",
                    title: "Webhook",
                    icon: Webhook,
                    variant: "ghost",
                  },
                  {
                    type: "http_request",
                    title: "HTTP Request",
                    icon: Globe,
                    variant: "ghost",
                  },
                  {
                    type: "data_transform",
                    title: "Data Transform",
                    icon: Blend,
                    variant: "ghost",
                  },
                  {
                    title: "Condition",
                    icon: Split,
                    variant: "ghost",
                    hierarchy: "group",
                  },
                  {
                    type: "condition.compare",
                    title: "Compare",
                    icon: GitCompareArrows,
                    variant: "ghost",
                    hierarchy: "groupItem",
                  },
                  {
                    type: "condition.regex",
                    title: "Regex",
                    icon: Regex,
                    variant: "ghost",
                    hierarchy: "groupItem",
                  },
                  {
                    type: "condition.membership",
                    title: "Membership",
                    icon: Container,
                    variant: "ghost",
                    hierarchy: "groupItem",
                  },
                  {
                    type: "open_case",
                    title: "Open Case",
                    icon: ShieldAlert,
                    variant: "ghost",
                  },
                  {
                    type: "receive_email",
                    title: "Receive Email",
                    icon: Mail,
                    variant: "ghost",
                  },
                  {
                    type: "send_email",
                    title: "Send Email",
                    icon: Send,
                    variant: "ghost",
                  },
                  {
                    title: "AI Actions",
                    icon: Sparkles,
                    variant: "ghost",
                    hierarchy: "group",
                  },
                  {
                    type: "llm.extract",
                    title: "Extract",
                    icon: FlaskConical,
                    variant: "ghost",
                    hierarchy: "groupItem",
                  },
                  {
                    type: "llm.label",
                    title: "Label",
                    icon: Tags,
                    variant: "ghost",
                    hierarchy: "groupItem",
                  },
                  {
                    type: "llm.translate",
                    title: "Translate",
                    icon: Languages,
                    variant: "ghost",
                    hierarchy: "groupItem",
                  },
                  {
                    type: "llm.choice",
                    title: "Choice",
                    icon: CheckSquare,
                    variant: "ghost",
                    hierarchy: "groupItem",
                  },
                  {
                    type: "llm.summarize",
                    title: "Summarize",
                    icon: BookText,
                    variant: "ghost",
                    hierarchy: "groupItem",
                  },
                ]}
              />
              {/* For items that should align at the end of the side nav */}
              {/* <div className="flex h-full flex-col items-start justify-end">
              </div> */}
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
