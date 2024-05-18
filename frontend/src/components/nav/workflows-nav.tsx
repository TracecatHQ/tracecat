"use client"

import React from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useWorkflowMetadata } from "@/providers/workflow"
import { ShieldAlertIcon, RadioIcon, WorkflowIcon } from "lucide-react"

import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import WorkflowSwitcher from "@/components/nav/workflow-switcher"

export default function WorkflowsNavbar() {
  const { workflowId, isLoading, isOnline, setIsOnline } = useWorkflowMetadata()

  if (isLoading) {
    return null
  }
  return (
    workflowId && (
      <div className="flex w-full items-center space-x-8">
        <WorkflowSwitcher />
        <TabSwitcher workflowId={workflowId} />
        <div className="flex flex-1 items-center justify-end space-x-3">
          <Switch
            id="enable-workflow"
            checked={isOnline}
            onCheckedChange={setIsOnline}
            className="data-[state=checked]:bg-green-500"
          />
          <Label
            className="flex text-xs text-muted-foreground"
            htmlFor="enable-workflow"
          >
            <RadioIcon className="mr-2 h-4 w-4" />
            <span>Publish workflow</span>
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
      <TabsList className="grid w-full grid-cols-2">
        <TabsTrigger className="w-full py-0 px-4" value="workflow" asChild>
          <Link
            href={`/workflows/${workflowId}`}
            className="h-full w-full text-xs"
            passHref
          >
            <WorkflowIcon className="mr-2 h-4 w-4" />
            <span>Workflow</span>
          </Link>
        </TabsTrigger>
        <TabsTrigger className="w-full py-0 px-4" value="cases" asChild>
          <Link
            href={`/workflows/${workflowId}/cases`}
            className="h-full w-full text-xs"
            passHref
          >
            <ShieldAlertIcon className="mr-2 h-4 w-4" />
            <span>Cases</span>
          </Link>
        </TabsTrigger>
        {/* <TabsTrigger className="w-full py-0" value="console" asChild>
          <Link
            href={`/workflows/${workflowId}/console`}
            className="h-full w-full"
            passHref
          >
            <SquareTerminal className="mr-2 h-4 w-4" />
            <span>Console</span>
          </Link>
        </TabsTrigger> */}
      </TabsList>
    </Tabs>
  )
}
