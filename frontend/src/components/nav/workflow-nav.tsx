"use client"

import React from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useWorkflow } from "@/providers/workflow"
import {
  GitPullRequestCreateArrowIcon,
  RadioIcon,
  ShieldAlertIcon,
  SquarePlay,
  WorkflowIcon,
} from "lucide-react"

import { Badge } from "@/components/ui/badge"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"

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
      <div className="flex flex-1 items-center justify-end space-x-6">
        <Button
          variant="outline"
          onClick={handleCommit}
          className="h-7 text-xs text-muted-foreground hover:bg-emerald-500 hover:text-white"
        >
          <GitPullRequestCreateArrowIcon className="mr-2 size-4" />
          Commit
        </Button>
        <Badge
          variant="outline"
          className="h-7 text-xs font-normal text-muted-foreground hover:cursor-default"
        >
          {workflow.version ? `v${workflow.version}` : "Not Committed"}
        </Badge>

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
          <RadioIcon className="mr-2 size-4" />
          <span>Enable workflow</span>
        </Label>
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
