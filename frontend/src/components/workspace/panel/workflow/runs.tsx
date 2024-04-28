"use client"

import { useEffect, useState } from "react"
import * as React from "react"

import "@radix-ui/react-dialog"

import { UpdateIcon } from "@radix-ui/react-icons"
import { ScrollArea } from "@radix-ui/react-scroll-area"
import { useQuery } from "@tanstack/react-query"
import { CircleCheck, CircleX, Loader, Loader2 } from "lucide-react"
import SyntaxHighlighter from "react-syntax-highlighter"
import { atomOneDark } from "react-syntax-highlighter/dist/esm/styles/hljs"

import { ActionRun, RunStatus, WorkflowRun } from "@/types/schemas"
import { fetchWorkflowRun, fetchWorkflowRuns } from "@/lib/flow"
import { cn, parseActionRunId, undoSlugify } from "@/lib/utils"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
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
  const {
    data: workflowRuns,
    isLoading,
    error,
  } = useQuery<WorkflowRun[], Error>({
    queryKey: ["workflow", workflowId, "runs"],
    queryFn: async ({ queryKey }) => {
      const [, workflowId] = queryKey as [string?, string?, string?]
      if (!workflowId) {
        throw new Error("No workflow ID provided")
      }
      return await fetchWorkflowRuns(workflowId)
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
                <WorkflowRunItem key={index} {...props} />
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
  status,
  id: workflowRunId,
  workflow_id: workflowId,
  created_at,
  updated_at,
}: React.PropsWithoutRef<WorkflowRun>) {
  const [open, setOpen] = useState(false)
  const [actionRuns, setActionRuns] = useState<ActionRun[]>([])
  const handleClick = () => setOpen(!open)

  useEffect(() => {
    if (open) {
      fetchWorkflowRun(workflowId, workflowRunId).then((res) =>
        setActionRuns(res?.action_runs ?? [])
      )
    }
  }, [open])
  const { icon, style } = getRunStatusStyle(status)

  return (
    <AccordionItem value={created_at.toString()}>
      <AccordionTrigger onClick={handleClick}>
        <div className="mr-2 flex w-full items-center justify-between">
          <DecoratedHeader
            size="sm"
            node={`${created_at.toLocaleDateString()}, ${created_at.toLocaleTimeString()}`}
            icon={icon}
            iconProps={{
              className: cn("stroke-2", style),
            }}
            className="font-medium"
          />
          <span className="flex items-center justify-center text-xs text-muted-foreground">
            <UpdateIcon className="mr-1 h-3 w-3" />
            {updated_at.toLocaleTimeString()}
          </span>
        </div>
      </AccordionTrigger>
      <AccordionContent className="space-y-2 pl-2">
        <Separator className="mb-4" />
        {actionRuns.map(
          (
            { id, created_at, updated_at, status, error_msg, result },
            index
          ) => {
            const { icon, style } = getRunStatusStyle(status)
            return (
              <Popover key={index}>
                <PopoverTrigger asChild>
                  <Button
                    variant="ghost"
                    className="mr-2 flex w-full items-center justify-between"
                  >
                    <div className="mr-2 flex w-full items-center justify-between">
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
                              {undoSlugify(parseActionRunId(id))}
                            </span>
                          </span>
                        }
                        icon={icon}
                        iconProps={{
                          className: cn("stroke-2", style),
                        }}
                      />
                      <span className="flex items-center justify-center text-xs text-muted-foreground">
                        <UpdateIcon className="mr-1 h-3 w-3" />
                        {updated_at.toLocaleTimeString()}
                      </span>
                    </div>
                  </Button>
                </PopoverTrigger>
                <PopoverContent
                  side="left"
                  sideOffset={20}
                  align="start"
                  className="m-0 w-[500px] rounded-md border-none p-0"
                >
                  <Card className="rounded-md p-1">
                    {result ? (
                      <SyntaxHighlighter
                        language={result ? "json" : undefined}
                        style={atomOneDark}
                        wrapLines
                        wrapLongLines={error_msg ? true : false}
                        customStyle={{
                          width: "100%",
                          maxWidth: "100%",
                          overflowX: "auto",
                        }}
                        codeTagProps={{
                          className:
                            "text-xs text-background rounded-md max-w-full overflow-auto",
                        }}
                        {...{
                          className:
                            "rounded-md p-4 overflow-auto max-w-full w-full no-scrollbar",
                        }}
                      >
                        {JSON.stringify(result, null, 2)}
                      </SyntaxHighlighter>
                    ) : (
                      <pre className="h-full w-full overflow-auto text-wrap rounded-md bg-[#292c33] p-2">
                        <code className="max-w-full overflow-auto rounded-md text-xs text-red-400/80">
                          {error_msg}
                        </code>
                      </pre>
                    )}
                  </Card>
                </PopoverContent>
              </Popover>
            )
          }
        )}
      </AccordionContent>
    </AccordionItem>
  )
}

export function getRunStatusStyle(status: RunStatus) {
  switch (status) {
    case "success":
      return { icon: CircleCheck, style: "fill-green-500/50 stroke-green-700" }
    case "failure":
      return { icon: CircleX, style: "fill-red-500/50 stroke-red-700" }
    case "running":
      return {
        icon: Loader2,
        style: "stroke-blue-500/50 animate-spin animate-[spin_1s_infinite]",
      }
    case "pending":
      return {
        icon: Loader,
        style:
          "stroke-blue-500/50 animate-spin animate-[spin_2s_linear_infinite]",
      }
    case "canceled":
      return { icon: CircleX, style: "fill-red-500/50 stroke-red-700" }
    default:
      throw new Error("Invalid status")
  }
}
