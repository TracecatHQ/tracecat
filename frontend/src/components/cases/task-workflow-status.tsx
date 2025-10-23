"use client"

import { Check, Loader2, Play, XCircle } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"
import type { CaseTaskRead } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useToast } from "@/components/ui/use-toast"
import {
  useCompactWorkflowExecution,
  useCreateManualWorkflowExecution,
  useWorkflowExecution,
} from "@/lib/hooks"

interface TaskWorkflowStatusProps {
  task: CaseTaskRead
  caseId: string
  executionId: string | null
  onExecutionStart: (taskId: string, execId: string) => void
}

export function TaskWorkflowStatus({
  task,
  caseId,
  executionId,
  onExecutionStart,
}: TaskWorkflowStatusProps) {
  const { toast } = useToast()
  const [tooltipOpen, setTooltipOpen] = useState(false)

  const { createExecution, createExecutionIsPending } =
    useCreateManualWorkflowExecution(task.workflow_id || "")

  const { execution, executionIsLoading, executionError } =
    useCompactWorkflowExecution(executionId || undefined)

  const encodedId = executionId ? encodeURIComponent(executionId) : ""
  const { execution: fullExecution } = useWorkflowExecution(encodedId, {
    enabled: tooltipOpen && execution?.status === "COMPLETED" && !!executionId,
  })
  const prevStatusRef = useRef<string | undefined>()

  useEffect(() => {
    if (!execution) {
      return
    }
    const prevStatus = prevStatusRef.current
    const currentStatus = execution.status

    if (prevStatus && prevStatus !== currentStatus) {
      const failureStatuses = ["FAILED", "TIMED_OUT", "CANCELED", "TERMINATED"]

      if (currentStatus === "COMPLETED") {
        toast({
          title: "Workflow completed",
          description: "The workflow executed successfully",
        })
      } else if (failureStatuses.includes(currentStatus)) {
        toast({
          title: "Workflow failed",
          description: `Execution ${currentStatus.toLowerCase()}`,
          variant: "destructive",
        })
      }
    }

    prevStatusRef.current = currentStatus
  }, [execution?.status])

  const handleRunWorkflow = useCallback(async () => {
    if (!task.workflow_id) return

    try {
      const result = await createExecution({
        workflow_id: task.workflow_id,
      })

      onExecutionStart(task.id, result.wf_exec_id)
    } catch (error) {
      toast({
        title: "Failed to start workflow",
        description:
          error instanceof Error ? error.message : "An error occurred",
        variant: "destructive",
      })
    }
  }, [task.workflow_id, task.id, createExecution, toast, onExecutionStart])

  if (
    createExecutionIsPending ||
    executionIsLoading ||
    execution?.status === "RUNNING"
  ) {
    return (
      <div className="flex h-6 w-6 items-center justify-center">
        <Loader2 className="h-4 w-4 animate-spin text-blue-500" />
      </div>
    )
  }

  if (execution?.status === "COMPLETED") {
    const completedWorkflowEvent = fullExecution?.events?.find(
      (event) => event.event_type === "WORKFLOW_EXECUTION_COMPLETED"
    )
    const result = completedWorkflowEvent?.result

    return (
      <TooltipProvider>
        <Tooltip open={tooltipOpen} onOpenChange={setTooltipOpen}>
          <TooltipTrigger asChild>
            <div className="flex h-6 w-6 items-center justify-center">
              <span
                className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-green-500 cursor-pointer"
                onClick={(e) => e.preventDefault()}
              >
                <Check className="h-3 w-3 text-white" />
              </span>
            </div>
          </TooltipTrigger>
          <TooltipContent
            side="left"
            className="max-w-sm max-h-64 overflow-hidden bg-white border border-border shadow-lg"
          >
            {result !== null && result !== undefined ? (
              <div className="space-y-1">
                <div className="text-xs font-medium text-foreground">
                  Workflow Result:
                </div>
                <pre className="text-xs overflow-auto max-h-48 bg-muted/30 p-2 rounded border border-border text-foreground">
                  {JSON.stringify(result, null, 2)}
                </pre>
              </div>
            ) : (
              <div className="text-xs text-muted-foreground">
                No result available
              </div>
            )}
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>
    )
  }

  const isErrorState =
    execution?.status === "FAILED" ||
    execution?.status === "TIMED_OUT" ||
    execution?.status === "CANCELED" ||
    execution?.status === "TERMINATED" ||
    (executionError && executionId)

  if (isErrorState) {
    return (
      <div className="flex h-6 w-6 items-center justify-center">
        <XCircle className="h-4 w-4 text-red-500" />
      </div>
    )
  }

  if (executionId && !execution) {
    return (
      <div className="flex h-6 w-6 items-center justify-center">
        <Loader2 className="h-4 w-4 animate-spin text-blue-500" />
      </div>
    )
  }

  if (task.workflow_id) {
    return (
      <Button
        variant="ghost"
        size="sm"
        className="h-6 w-6 p-0"
        onClick={(e) => {
          e.stopPropagation()
          handleRunWorkflow()
        }}
      >
        <Play className="h-3 w-3" />
      </Button>
    )
  }

  return null
}
