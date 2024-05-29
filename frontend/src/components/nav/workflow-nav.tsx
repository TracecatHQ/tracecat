"use client"

import React from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useWorkflowMetadata } from "@/providers/workflow"
import { RadioIcon, ShieldAlertIcon, WorkflowIcon } from "lucide-react"

import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"

export default function WorkflowNav() {
  const { workflow, workflowId, isLoading, isOnline, setIsOnline } =
    useWorkflowMetadata()

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
                <BreadcrumbSeparator className="font-semibold">
                  {"/"}
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
            className="data-[state=checked]:bg-emerald-500"
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
        <TabsTrigger className="w-full px-4 py-0" value="workflow" asChild>
          <Link
            href={`/workflows/${workflowId}`}
            className="size-full text-sm"
            passHref
          >
            <WorkflowIcon className="mr-2 h-4 w-4" />
            <span>Workflow</span>
          </Link>
        </TabsTrigger>
        <TabsTrigger className="w-full px-4 py-0" value="cases" asChild>
          <Link
            href={`/workflows/${workflowId}/cases`}
            className="size-full text-sm"
            passHref
          >
            <ShieldAlertIcon className="mr-2 h-4 w-4" />
            <span>Cases</span>
          </Link>
        </TabsTrigger>
        {/* <TabsTrigger className="w-full py-0" value="console" asChild>
          <Link
            href={`/workflows/${workflowId}/console`}
            className="size-full"
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
