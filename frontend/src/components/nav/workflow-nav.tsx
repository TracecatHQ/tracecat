"use client"

import React from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useWorkflow } from "@/providers/workflow"
import {
  GitPullRequestCreateArrowIcon,
  ShieldAlertIcon,
  SquarePlay,
  WorkflowIcon,
} from "lucide-react"

import { cn } from "@/lib/utils"
import { Badge } from "@/components/ui/badge"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { ConfirmationDialog } from "@/components/confirmation-dialog"

export default function WorkflowNav() {
  const { workflow, isLoading, isOnline, setIsOnline, commit } = useWorkflow()

  const handleCommit = async () => {
    console.log("Committing changes...")
    await commit()
  }

  if (!workflow || isLoading) {
    return null
  }

  return (
    <div className="flex w-full items-center space-x-8">
      <Breadcrumb>
        <BreadcrumbList>
          <BreadcrumbItem>
            <BreadcrumbLink href="/workflows">Workflows</BreadcrumbLink>
          </BreadcrumbItem>
          <BreadcrumbSeparator className="font-semibold">
            {"/"}
          </BreadcrumbSeparator>
          <BreadcrumbItem>{workflow.title}</BreadcrumbItem>
        </BreadcrumbList>
      </Breadcrumb>
      <TabSwitcher workflowId={workflow.id} />
      {/* Commit button */}
      <div className="flex flex-1 items-center justify-end space-x-6">
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              variant="outline"
              onClick={handleCommit}
              className="h-7 text-xs text-muted-foreground hover:bg-emerald-500 hover:text-white"
            >
              <GitPullRequestCreateArrowIcon className="mr-2 size-4" />
              Commit
            </Button>
          </TooltipTrigger>
          <TooltipContent
            side="bottom"
            className="max-w-48 border bg-background text-xs text-muted-foreground shadow-lg"
          >
            Create workflow definition v{(workflow.version || 0) + 1} with your
            changes.
          </TooltipContent>
        </Tooltip>

        <Badge
          variant="outline"
          className="h-7 text-xs font-normal text-muted-foreground hover:cursor-default"
        >
          {workflow.version ? `v${workflow.version}` : "Not Committed"}
        </Badge>

        {/* Workflow activation */}
        <Tooltip>
          <TooltipTrigger>
            <ConfirmationDialog
              title={isOnline ? "Disable Workflow?" : "Enable Workflow?"}
              description={
                isOnline
                  ? "Are you sure you want to disable the workflow? This will stop new executions and event processing."
                  : "Are you sure you want to enable the workflow? This will start new executions and event processing."
              }
              onConfirm={() => setIsOnline(!isOnline)}
            >
              <Button
                variant="outline"
                className={cn(
                  "h-7 text-xs font-bold",
                  isOnline
                    ? "text-rose-400 hover:text-rose-600"
                    : "bg-emerald-500 text-white hover:bg-emerald-500/80 hover:text-white"
                )}
              >
                {isOnline ? "Disable Workflow" : "Enable Workflow"}
              </Button>
            </ConfirmationDialog>
          </TooltipTrigger>
          <TooltipContent
            side="bottom"
            className="max-w-48 border bg-background text-xs text-muted-foreground shadow-lg"
          >
            {isOnline ? "Disable" : "Enable"} the workflow to{" "}
            {isOnline ? "stop" : "start"} new executions and receive events.
          </TooltipContent>
        </Tooltip>
      </div>
    </div>
  )
}

function TabSwitcher({ workflowId }: { workflowId: string }) {
  const pathname = usePathname()
  let leafRoute: string = "workflow"
  if (pathname.endsWith("cases")) {
    leafRoute = "cases"
  } else if (pathname.endsWith("executions")) {
    leafRoute = "executions"
  }

  return (
    <Tabs value={leafRoute}>
      <TabsList className="grid w-full grid-cols-3">
        <TabsTrigger className="w-full px-4 py-0" value="workflow" asChild>
          <Link
            href={`/workflows/${workflowId}`}
            className="size-full text-sm"
            passHref
          >
            <WorkflowIcon className="mr-2 size-4" />
            <span>Workflow</span>
          </Link>
        </TabsTrigger>
        <TabsTrigger className="w-full px-4 py-0" value="cases" asChild>
          <Link
            href={`/workflows/${workflowId}/cases`}
            className="size-full text-sm"
            passHref
          >
            <ShieldAlertIcon className="mr-2 size-4" />
            <span>Cases</span>
          </Link>
        </TabsTrigger>
        <TabsTrigger className="w-full px-4 py-0" value="executions" asChild>
          <Link
            href={`/workflows/${workflowId}/executions`}
            className="size-full text-sm"
            passHref
          >
            <SquarePlay className="mr-2 size-4" />
            <span>Runs</span>
          </Link>
        </TabsTrigger>
      </TabsList>
    </Tabs>
  )
}
