"use client"

import React from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useWorkflowMetadata } from "@/providers/workflow"
import { BellRingIcon, SquareTerminal, WorkflowIcon } from "lucide-react"

import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import WorkflowSwitcher from "@/components/nav/workflow-switcher"

interface NavbarProps extends React.HTMLAttributes<HTMLDivElement> {}

export default function WorkflowsNavbar(props: NavbarProps) {
  const { workflowId, isLoading, isOnline, setIsOnline } = useWorkflowMetadata()

  if (isLoading) {
    return null
  }
  return (
    workflowId && (
      <div className="flex w-full items-center space-x-8">
        <WorkflowSwitcher />
        <TabSwitcher workflowId={workflowId} />
        <div className="flex flex-1 items-center justify-end space-x-2">
          <Switch
            id="enable-workflow"
            checked={isOnline}
            onCheckedChange={setIsOnline}
            className="data-[state=checked]:bg-green-500"
          />
          <Label
            className="w-30 text-xs text-muted-foreground"
            htmlFor="enable-workflow"
          >
            {isOnline ? "Pause" : "Publish"}
          </Label>
        </div>
      </div>
    )
  )
}

function TabSwitcher({ workflowId }: { workflowId: string }) {
  const pathname = usePathname()
  const leafRoute = pathname.endsWith("cases")
    ? "cases"
    : pathname.endsWith("console")
      ? "console"
      : "workflow"
  return (
    <Tabs value={leafRoute}>
      <TabsList className="grid w-full grid-cols-3">
        <TabsTrigger className="w-full py-0" value="workflow" asChild>
          <Link
            href={`/workflows/${workflowId}`}
            className="h-full w-full"
            passHref
          >
            <WorkflowIcon className="mr-2 h-4 w-4" />
            <span>Workflow</span>
            <kbd className="ml-4 flex items-center justify-center gap-1 rounded border bg-muted px-1 font-mono text-[10px] font-medium text-muted-foreground opacity-100">
              <span>Alt+F</span>
            </kbd>
          </Link>
        </TabsTrigger>
        <TabsTrigger className="w-full py-0" value="cases" asChild>
          <Link
            href={`/workflows/${workflowId}/cases`}
            className="h-full w-full"
            passHref
          >
            <BellRingIcon className="mr-2 h-4 w-4" />
            <span>Cases</span>
            <kbd className="ml-4 flex items-center justify-center gap-1 rounded border bg-muted px-1 font-mono text-[10px] font-medium text-muted-foreground opacity-100">
              <span>Alt+C</span>
            </kbd>
          </Link>
        </TabsTrigger>
        <TabsTrigger className="w-full py-0" value="console" asChild>
          <Link
            href={`/workflows/${workflowId}/console`}
            className="h-full w-full"
            passHref
          >
            <SquareTerminal className="mr-2 h-4 w-4" />
            <span>Console</span>
            <kbd className="ml-4 flex items-center justify-center gap-1 rounded border bg-muted px-1 font-mono text-[10px] font-medium text-muted-foreground opacity-100">
              <span>Alt+L</span>
            </kbd>
          </Link>
        </TabsTrigger>
      </TabsList>
    </Tabs>
  )
}
