"use client"

import React from "react"
import {
  EventHistoryResponse,
  WorkflowExecutionResponse,
  workflowExecutionsTerminateWorkflowExecution,
} from "@/client"
import {
  CircleCheck,
  CircleMinusIcon,
  CircleX,
  CircleXIcon,
  Loader2,
} from "lucide-react"

import { cn, undoSlugify } from "@/lib/utils"
import { buttonVariants } from "@/components/ui/button"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Label } from "@/components/ui/label"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import NoContent from "@/components/no-content"

import "react18-json-view/src/style.css"

import { useRouter } from "next/navigation"
import { useWorkspace } from "@/providers/workspace"
import { TriangleRightIcon } from "@radix-ui/react-icons"

import { ToastAction } from "@/components/ui/toast"
import { toast } from "@/components/ui/use-toast"

/**
 * The top-level view of workflow executions (shows each execution and its status)
 * @param param0
 * @returns
 */
export function WorkflowExecutionNav({
  executions: workflowExecutions,
  executionId,
  setExecutionId,
  setSelectedEvent,
}: {
  executions?: WorkflowExecutionResponse[]
  isCollapsed?: boolean
  executionId?: string
  setExecutionId: (id?: string) => void
  setSelectedEvent: (event?: EventHistoryResponse) => void
}) {
  const router = useRouter()
  const { workspaceId } = useWorkspace()
  if (!workflowExecutions) {
    return <NoContent message="No workflow executions found." />
  }

  const handleTerminateExecuton = async (executionId: string) => {
    console.log("Terminate execution")
    try {
      await workflowExecutionsTerminateWorkflowExecution({
        workspaceId,
        executionId,
        requestBody: {
          reason: "User terminated execution",
        },
      })
      toast({
        title: "Successfully requested termination",
        description: `Execution ${executionId} has been terminated. You can refresh the page to see the updated status.`,
        action: (
          <ToastAction
            altText="Refresh"
            onClick={() => window.location.reload()}
          >
            Refresh
          </ToastAction>
        ),
      })
      router.refresh()
    } catch (error) {
      console.error(error)
      toast({
        title: "Failed to terminate execution",
        description: `Execution ${executionId} could not be terminated. Please try again
              later.`,
      })
    }
  }

  return (
    <div
      data-collapsed={false}
      className="group flex flex-col gap-4 py-2 data-[collapsed=true]:py-2"
    >
      <nav className="grid gap-1 px-2 group-[[data-collapsed=true]]:justify-center group-[[data-collapsed=true]]:px-2">
        {workflowExecutions.map((execution, index) => (
          <HoverCard openDelay={10} closeDelay={10} key={index}>
            <div
              key={index}
              className={cn(
                buttonVariants({ variant: "default", size: "sm" }),
                "justify-start bg-background text-muted-foreground shadow-none hover:cursor-default hover:bg-gray-100",
                execution.id === executionId && "bg-gray-200"
              )}
              onClick={() => {
                setSelectedEvent(undefined)
                setExecutionId(execution.id)
              }}
            >
              <div className="flex items-center">
                <WorkflowExecutionStatusIcon
                  status={execution.status}
                  className="size-4"
                />
                <span className="ml-2">
                  {new Date(execution.start_time).toLocaleString()}
                </span>
              </div>
              <div className="ml-auto">
                <HoverCardTrigger asChild>
                  <div className="flex items-center justify-center">
                    {execution.status === "RUNNING" ? (
                      <Tooltip>
                        <TooltipTrigger asChild>
                          <CircleXIcon
                            className="mr-1 size-4 fill-muted-foreground/70 stroke-white transition-all hover:cursor-pointer hover:fill-rose-500"
                            onClick={async () =>
                              await handleTerminateExecuton(execution.id)
                            }
                          />
                        </TooltipTrigger>
                        <TooltipContent
                          side="left"
                          className="flex items-center gap-4  shadow-lg"
                        >
                          <span>Terminate Run</span>
                        </TooltipContent>
                      </Tooltip>
                    ) : (
                      <TriangleRightIcon className="m-0 size-6 p-0 text-muted-foreground/70" />
                    )}
                  </div>
                </HoverCardTrigger>
              </div>
            </div>
            <HoverCardContent
              className="w-100"
              side="right"
              align="start"
              sideOffset={30}
              alignOffset={-10}
            >
              <div className="flex flex-col items-start justify-between space-y-2 text-start text-xs">
                <div className="flex flex-col">
                  <Label className="text-xs text-muted-foreground">
                    Execution ID
                  </Label>
                  <span>{execution.id.split(":")[1]}</span>
                </div>
                <div className="flex flex-col">
                  <Label className="text-xs text-muted-foreground">
                    Run ID
                  </Label>
                  <span>{execution.run_id}</span>
                </div>
                <div className="flex flex-col">
                  <Label className="text-xs text-muted-foreground">
                    Start Time
                  </Label>
                  <span>{new Date(execution.start_time).toLocaleString()}</span>
                </div>
                <div className="flex flex-col">
                  <Label className="text-xs text-muted-foreground">
                    End Time
                  </Label>
                  <span>
                    {execution.close_time
                      ? new Date(execution.close_time).toLocaleString()
                      : "-"}
                  </span>
                </div>
              </div>
            </HoverCardContent>
          </HoverCard>
        ))}
      </nav>
    </div>
  )
}

export function WorkflowExecutionStatusIcon({
  status,
  className,
}: {
  status: WorkflowExecutionResponse["status"]
} & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        {getExecutionStatusIcon(status, className)}
      </TooltipTrigger>
      <TooltipContent side="top" className="flex items-center gap-4  shadow-lg">
        <span>{undoSlugify(status.toLowerCase())}</span>
      </TooltipContent>
    </Tooltip>
  )
}
export function getExecutionStatusIcon(
  status: WorkflowExecutionResponse["status"],
  className?: string
) {
  switch (status) {
    case "COMPLETED":
      return (
        <CircleCheck
          className={cn(
            "border-none border-emerald-500 fill-emerald-500 stroke-white",
            className
          )}
        />
      )
    case "FAILED":
      return <CircleX className={cn("fill-rose-500 stroke-white", className)} />
    case "RUNNING":
      return (
        <Loader2 className={cn("animate-spin stroke-blue-500/50", className)} />
      )
    case "TERMINATED":
      return (
        <CircleMinusIcon
          className={cn("fill-rose-500 stroke-white", className)}
        />
      )
    case "CANCELED":
      return (
        <CircleMinusIcon
          className={cn("fill-orange-500 stroke-white", className)}
        />
      )
    default:
      throw new Error("Invalid status")
  }
}
