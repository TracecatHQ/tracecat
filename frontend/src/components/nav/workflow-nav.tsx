"use client"

import React from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useWorkflowMetadata } from "@/providers/workflow"
import { Slash, ShieldAlertIcon, RadioIcon, WorkflowIcon } from "lucide-react"

import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbSeparator } from "@/components/ui/breadcrumb";


export default function WorkflowNav() {
  const { workflow, workflowId, isLoading, isOnline, setIsOnline } = useWorkflowMetadata()

  if (!workflow || isLoading) {
    return null
  }
  return (
    workflowId && (
      <div className="flex w-full items-center space-x-8">
        <Breadcrumb>
          <BreadcrumbList>
            <BreadcrumbItem>
              <BreadcrumbLink href="/workflows">Workflows</BreadcrumbLink>
            </BreadcrumbItem>
            {workflow && (
              <>
                <BreadcrumbSeparator>
                  <Slash />
                </BreadcrumbSeparator>
                <BreadcrumbItem>{workflow.title}</BreadcrumbItem>
              </>
            )}
          </BreadcrumbList>
        </Breadcrumb>
        <TabSwitcher workflowId={workflowId} />
        <div className="flex flex-1 items-center justify-end space-x-3">
          <Switch
            id="enable-workflow"
            checked={isOnline}
            onCheckedChange={setIsOnline}
            className="data-[state=checked]:bg-lime-400"
          />
          <Label
            className="flex text-xs text-muted-foreground"
            htmlFor="enable-workflow"
          >
            <RadioIcon className="mr-2 h-4 w-4" />
            <span>Enable workflow</span>
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
            className="h-full w-full text-sm"
            passHref
          >
            <WorkflowIcon className="mr-2 h-4 w-4" />
            <span>Workflow</span>
          </Link>
        </TabsTrigger>
        <TabsTrigger className="w-full py-0 px-4" value="cases" asChild>
          <Link
            href={`/workflows/${workflowId}/cases`}
            className="h-full w-full text-sm"
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
