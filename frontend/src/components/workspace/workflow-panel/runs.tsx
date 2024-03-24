"use client"

import { useEffect, useState } from "react"
import * as React from "react"
import { useSession } from "@/providers/session"

import "@radix-ui/react-dialog"

import { ScrollArea } from "@radix-ui/react-scroll-area"
import { useQuery } from "@tanstack/react-query"
import { CircleCheck, CircleX, Loader2 } from "lucide-react"

import { ActionRun, RunStatus, WorkflowRun } from "@/types/schemas"
import { fetchWorkflowRun, fetchWorkflowRuns } from "@/lib/flow"
import { cn, parseActionRunId } from "@/lib/utils"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Separator } from "@/components/ui/separator"
import DecoratedHeader from "@/components/decorated-header"
import { CenteredSpinner } from "@/components/loading/spinner"
import NoContent from "@/components/no-content"
import { AlertNotification } from "@/components/notifications"

export function WorkflowRunsView({
  workflowId,
  className,
}: {
  workflowId: string
  className?: string
}) {
  const session = useSession()

  const {
    data: workflowRuns,
    isLoading,
    error,
  } = useQuery<WorkflowRun[], Error>({
    queryKey: ["workflow", workflowId, "runs"],
    queryFn: async ({ queryKey }) => {
      const [_workflow, workflowId, _run] = queryKey as [
        string?,
        string?,
        string?,
      ]
      if (!workflowId) {
        throw new Error("No workflow ID provided")
      }
      const data = await fetchWorkflowRuns(session, workflowId)
      return data
    },
  })
  return (
    <div className="space-y-3">
      <h1 className="text-xs font-medium">Past Runs</h1>
      <ScrollArea
        className={cn(
          "h-full max-h-[400px] overflow-y-auto rounded-md border p-4",
          className
        )}
      >
        {isLoading ? (
          <CenteredSpinner />
        ) : error ? (
          <AlertNotification
            level="error"
            message="Error loading workflow runs"
          />
        ) : workflowRuns && workflowRuns.length > 0 ? (
          <Accordion type="single" collapsible className="w-full">
            {workflowRuns
              ?.sort((a, b) => b.created_at.getTime() - a.created_at.getTime())
              .map((props, index) => (
                <WorkflowRunItem
                  className="my-2 w-full"
                  key={index}
                  {...props}
                />
              ))}
          </Accordion>
        ) : (
          <NoContent className="my-8" message="No runs available" />
        )}
      </ScrollArea>
    </div>
  )
}

function WorkflowRunItem({
  className,
  status,
  id: workflowRunId,
  workflow_id: workflowId,
  created_at,
  updated_at,
  ...props
}: React.PropsWithoutRef<WorkflowRun> & React.HTMLAttributes<HTMLDivElement>) {
  const session = useSession()
  const [open, setOpen] = useState(false)
  const [actionRuns, setActionRuns] = useState<ActionRun[]>([])
  const handleClick = () => setOpen(!open)

  useEffect(() => {
    if (open) {
      fetchWorkflowRun(session, workflowId, workflowRunId).then((res) =>
        setActionRuns(res.action_runs)
      )
    }
  }, [open])
  return (
    <AccordionItem value={created_at.toString()}>
      <AccordionTrigger onClick={handleClick}>
        <div className="mr-2 flex w-full items-center justify-between">
          <DecoratedHeader
            size="sm"
            node={`${created_at.toLocaleDateString()}, ${created_at.toLocaleTimeString()}`}
            icon={status === "success" ? CircleCheck : CircleX}
            iconProps={{
              className: cn(
                "stroke-2",
                status === "success"
                  ? "fill-green-500/50 stroke-green-700"
                  : "fill-red-500/50 stroke-red-700"
              ),
            }}
            className="font-medium capitalize"
          />
          <span className="text-xs text-muted-foreground">
            Updated: {updated_at.toLocaleTimeString()}
          </span>
        </div>
      </AccordionTrigger>
      <AccordionContent className="space-y-2 pl-2">
        <Separator className="mb-4" />
        {actionRuns.map(({ id, created_at, updated_at, status }, index) => {
          const { icon, style } = getStyle(status)
          return (
            <div
              key={index}
              className="mr-2 flex w-full items-center justify-between"
            >
              <DecoratedHeader
                size="sm"
                className="font-medium"
                node={
                  <span className="flex items-center text-xs">
                    <span>
                      {created_at.toLocaleDateString()}{" "}
                      {created_at.toLocaleTimeString()}
                    </span>
                    <span className="ml-4 font-normal">
                      {parseActionRunId(id)}
                    </span>
                  </span>
                }
                icon={icon}
                iconProps={{
                  className: cn(
                    "stroke-2",
                    style,
                    (status === "running" || status === "pending") &&
                      "animate-spin fill-background"
                  ),
                }}
              />
              <span className="text-xs text-muted-foreground">
                Updated: {updated_at.toLocaleTimeString()}
              </span>
            </div>
          )
        })}
      </AccordionContent>
    </AccordionItem>
  )
}

function getStyle(status: RunStatus) {
  switch (status) {
    case "success":
      return { icon: CircleCheck, style: "fill-green-500/50 stroke-green-700" }
    case "failure":
      return { icon: CircleX, style: "fill-red-500/50 stroke-red-700" }
    case "running":
      return {
        icon: Loader2,
        style: "stroke-yellow-500 animate-spin",
      }
    case "pending":
      return { icon: Loader2, style: "stroke-yellow-500 animate-spin" }
    case "canceled":
      return { icon: CircleX, style: "fill-red-500/50 stroke-red-700" }
    default:
      throw new Error("Invalid status")
  }
}
