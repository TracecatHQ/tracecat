"use client"

import { Check, Loader2, Play, XCircle } from "lucide-react"
import { useCallback, useEffect, useRef } from "react"
import type { CaseTaskRead } from "@/client"
import { Button } from "@/components/ui/button"
import { useToast } from "@/components/ui/use-toast"
import {
  useCompactWorkflowExecution,
  useCreateManualWorkflowExecution,
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

  const { createExecution, createExecutionIsPending } =
    useCreateManualWorkflowExecution(task.workflow_id || "")

  const { execution, executionIsLoading, executionError } =
    useCompactWorkflowExecution(executionId || undefined)

  const prevStatusRef = useRef<string | undefined>()

  useEffect(() => {
    if (!execution) {
      return
    }

    const prevStatus = prevStatusRef.current
    const currentStatus = execution.status

    if (prevStatus && prevStatus !== currentStatus) {
      if (currentStatus === "COMPLETED") {
        toast({
          title: "Workflow completed",
          description: "The workflow executed successfully",
        })
      } else if (
        currentStatus === "FAILED" ||
        currentStatus === "TIMED_OUT" ||
        currentStatus === "CANCELED" ||
        currentStatus === "TERMINATED"
      ) {
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

  if (createExecutionIsPending || executionIsLoading) {
    return <Loader2 className="h-4 w-4 animate-spin text-blue-500" />
  }

  if (execution?.status === "RUNNING") {
    return <Loader2 className="h-4 w-4 animate-spin text-blue-500" />
  }

  if (execution?.status === "COMPLETED") {
    return (
      <span className="inline-flex h-4 w-4 items-center justify-center rounded-full bg-green-500">
        <Check className="h-3 w-3 text-white" />
      </span>
    )
  }

  if (
    execution?.status === "FAILED" ||
    execution?.status === "TIMED_OUT" ||
    execution?.status === "CANCELED" ||
    execution?.status === "TERMINATED"
  ) {
    return <XCircle className="h-4 w-4 text-red-500" />
  }

  if (executionError) {
    return <XCircle className="h-4 w-4 text-red-500" />
  }

  if (executionId && !execution) {
    return <Loader2 className="h-4 w-4 animate-spin text-blue-500" />
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
