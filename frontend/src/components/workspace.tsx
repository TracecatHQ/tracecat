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

import { ActionTiles } from "@/components/action-tiles"
import { cn } from "@/lib/utils"
import { Separator } from "@/components/ui/separator"
import { TooltipProvider } from "@/components/ui/tooltip"
import { ResizableHandle, ResizablePanel, ResizablePanelGroup } from "@/components/ui/resizable"

interface WorkspaceProps {
  defaultLayout: number[] | undefined
  defaultCollapsed?: boolean
  navCollapsedSize: number
}

export function Workspace({
  defaultLayout = [265, 440, 655],
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
          <Separator />
          <ActionTiles
            isCollapsed={isCollapsed}
            links={[
              {
                title: "Webhook",
                label: "",
                icon: Webhook,
                variant: "ghost",
              },
              {
                title: "HTTP Request",
                label: "",
                icon: Globe,
                variant: "ghost",
              },
              {
                title: "Data Transform",
                label: "",
                icon: Blend,
                variant: "ghost",
              },
              {
                title: "If Condition",
                label: "",
                icon: Split,
                variant: "ghost",
              },
              {
                title: "Open Case",
                label: "",
                icon: ShieldAlert,
                variant: "ghost",
              },
              {
                title: "Receive Email",
                label: "",
                icon: Mail,
                variant: "ghost",
              },
              {
                title: "Send Email",
                label: "",
                icon: Send,
                variant: "ghost",
              },
              {
                title: "AI Copilot",
                label: "",
                icon: Sparkles,
                variant: "ghost",
              },
            ]}
          />
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel defaultSize={defaultLayout[1]} minSize={30}>
        </ResizablePanel>
        <ResizableHandle withHandle />
        <ResizablePanel defaultSize={defaultLayout[2]}>
        </ResizablePanel>
      </ResizablePanelGroup>
    </TooltipProvider>
  )
}
