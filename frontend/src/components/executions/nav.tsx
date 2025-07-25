"use client"

import {
  AlarmClockOffIcon,
  CircleArrowRightIcon,
  CircleCheck,
  CircleMinusIcon,
  CircleX,
  CircleXIcon,
} from "lucide-react"
import type React from "react"
import {
  type WorkflowExecutionReadMinimal,
  workflowExecutionsTerminateWorkflowExecution,
} from "@/client"
import NoContent from "@/components/no-content"
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
import { cn, undoSlugify } from "@/lib/utils"

import "react18-json-view/src/style.css"

import { TriangleRightIcon } from "@radix-ui/react-icons"
import Link from "next/link"
import { useParams, usePathname, useRouter } from "next/navigation"
import { Spinner } from "@/components/loading/spinner"
import { ToastAction } from "@/components/ui/toast"
import { toast } from "@/components/ui/use-toast"
import { parseExecutionId } from "@/lib/event-history"
import { useWorkspace } from "@/providers/workspace"

/**
 * The top-level view of workflow executions (shows each execution and its status)
 * @param param0
 * @returns
 */
export function WorkflowExecutionNav({
  executions: workflowExecutions,
}: {
  executions?: WorkflowExecutionReadMinimal[]
}) {
  const params = useParams<{ executionId: string }>()
  const currExecutionId = params?.executionId
  const currExecutionIdDecoded = currExecutionId
    ? decodeURIComponent(currExecutionId)
    : null
  const router = useRouter()
  const pathname = usePathname()
  const baseUrl = pathname ? pathname.split("/executions")[0] : ""
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
    <div className="group flex flex-col gap-4 py-2">
      <nav className="grid gap-1 px-2">
        {workflowExecutions.map((execution, index) => {
          const executionId = parseExecutionId(execution.id)[1]
          return (
            <HoverCard openDelay={10} closeDelay={10} key={index}>
              <Link
                href={`${baseUrl}/executions/${executionId}`}
                className={cn(
                  buttonVariants({ variant: "default", size: "sm" }),
                  "justify-start bg-background text-muted-foreground shadow-none hover:cursor-default hover:bg-gray-100",
                  executionId === currExecutionIdDecoded && "bg-gray-200"
                )}
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
                              className="terminate-button mr-1 size-4 fill-muted-foreground/70 stroke-white transition-all hover:cursor-pointer hover:fill-rose-500"
                              onClick={async (e) => {
                                e.stopPropagation()
                                await handleTerminateExecuton(execution.id)
                              }}
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
              </Link>
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
                    <span>{parseExecutionId(execution.id)[1]}</span>
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
                    <span>
                      {new Date(execution.start_time).toLocaleString()}
                    </span>
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
          )
        })}
      </nav>
    </div>
  )
}

export function WorkflowExecutionStatusIcon({
  status,
  className,
}: {
  status: WorkflowExecutionReadMinimal["status"]
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
  status: WorkflowExecutionReadMinimal["status"],
  className?: string
) {
  switch (status) {
    case "RUNNING":
      return <Spinner className={className} />
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
    case "CANCELED":
      return (
        <CircleMinusIcon
          className={cn("fill-orange-500 stroke-white", className)}
        />
      )
    case "TERMINATED":
      return (
        <CircleMinusIcon
          className={cn("fill-rose-500 stroke-white", className)}
        />
      )
    case "CONTINUED_AS_NEW":
      return (
        <CircleArrowRightIcon
          className={cn("fill-blue-500 stroke-white", className)}
        />
      )
    case "TIMED_OUT":
      return (
        <AlarmClockOffIcon
          className={cn("stroke-rose-500", className)}
          strokeWidth={2.5}
        />
      )
    default:
      throw new Error("Invalid status")
  }
}
