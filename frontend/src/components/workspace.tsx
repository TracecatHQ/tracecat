"use client"

import * as React from "react"
import {
  Blend,
  Globe,
  Mail,
  Send,
  ShieldAlert,
  Sparkles,
  Split,
  Webhook
} from "lucide-react"

import { ReactFlowProvider } from "reactflow";

import { ActionTiles } from "@/components/action-tiles"
import { WorkflowCanvas } from "@/components/canvas"
import { WorkflowPanel } from "@/components/panel"
import { WorkflowBuilderProvider } from "@/providers/flow"
import { cn } from "@/lib/utils"
import { TooltipProvider } from "@/components/ui/tooltip"
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable"

interface WorkspaceProps {
  defaultLayout: number[] | undefined
  defaultCollapsed?: boolean
  navCollapsedSize: number
}

export function Workspace({
  defaultLayout = [265, 440, 265],
  defaultCollapsed = false,
  navCollapsedSize,
}: WorkspaceProps) {
  const [isCollapsed, setIsCollapsed] = React.useState(defaultCollapsed)

  // Adjust onCollapse to match the expected signature
  const handleCollapse = () => {
    // Assuming you have a way to set the collapsed state here
    setIsCollapsed(true); // Set to true when you know the panel is collapsed
    document.cookie = `react-resizable-panels:collapsed=${JSON.stringify(true)}`;
  };

  // Adjust onExpand to match the expected signature
  const handleExpand = () => {
    // Assuming you have a way to set the collapsed state here
    setIsCollapsed(false); // Set to false when you know the panel is expanded
    document.cookie = `react-resizable-panels:collapsed=${JSON.stringify(false)}`;
  };

  return (
    <ReactFlowProvider>
      <WorkflowBuilderProvider>
        <TooltipProvider delayDuration={0}>
          <ResizablePanelGroup
            direction="horizontal"
            onLayout={(sizes: number[]) => {
              document.cookie = `react-resizable-panels:layout=${JSON.stringify(
                sizes
              )}`
            }}
            className="h-full max-h-[800px] items-stretch"
          >
            <ResizablePanel
              defaultSize={defaultLayout[0]}
              collapsedSize={navCollapsedSize}
              collapsible={true}
              minSize={15}
              maxSize={20}
              onCollapse={handleCollapse}
              onExpand={handleExpand}
              className={cn(isCollapsed && "min-w-[50px] transition-all duration-300 ease-in-out")}
            >
              <ActionTiles
                isCollapsed={isCollapsed}
                tiles={[
                  {
                    type: "Webhook",
                    title: "",
                    icon: Webhook,
                    variant: "ghost",
                  },
                  {
                    type: "HTTP Request",
                    title: "",
                    icon: Globe,
                    variant: "ghost",
                  },
                  {
                    type: "Data Transform",
                    title: "",
                    icon: Blend,
                    variant: "ghost",
                  },
                  {
                    type: "If Condition",
                    title: "",
                    icon: Split,
                    variant: "ghost",
                  },
                  {
                    type: "Open Case",
                    title: "",
                    icon: ShieldAlert,
                    variant: "ghost",
                  },
                  {
                    type: "Receive Email",
                    title: "",
                    icon: Mail,
                    variant: "ghost",
                  },
                  {
                    type: "Send Email",
                    title: "",
                    icon: Send,
                    variant: "ghost",
                  },
                  {
                    type: "AI Copilot",
                    title: "",
                    icon: Sparkles,
                    variant: "ghost",
                  },
                ]}
              />
            </ResizablePanel>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize={defaultLayout[1]}>
              <WorkflowCanvas />
            </ResizablePanel>
            <ResizableHandle withHandle />
            <ResizablePanel defaultSize={defaultLayout[2]} minSize={25}>
              <WorkflowPanel />
            </ResizablePanel>
          </ResizablePanelGroup>
        </TooltipProvider>
      </WorkflowBuilderProvider>
    </ReactFlowProvider>
  )
}
