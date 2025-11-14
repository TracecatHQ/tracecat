"use client"

import {
  AlertCircle,
  Check,
  Loader2,
  Play,
  PlayCircle,
  XCircle,
} from "lucide-react"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import type {
  CaseTaskRead,
  CaseTaskUpdate,
  WorkflowReadMinimal,
} from "@/client"
import { CodeEditor } from "@/components/editor/codemirror/code-editor"
import { JsonViewWithControls } from "@/components/json-viewer"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
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
  useUpdateCaseTask,
  useWorkflowExecution,
} from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

interface TaskWorkflowStatusProps {
  task: CaseTaskRead
  caseId: string
  executionId: string | null
  workflow: WorkflowReadMinimal
  onExecutionStart: (taskId: string, execId: string) => void
}

const FAILURE_STATUSES = new Set([
  "FAILED",
  "TIMED_OUT",
  "CANCELED",
  "TERMINATED",
])

const formatTriggerData = (
  value?: Record<string, unknown> | string | null
): string => {
  try {
    const payload =
      typeof value === "string" ? JSON.parse(value) : (value ?? {})

    if (
      typeof payload === "object" &&
      payload !== null &&
      !Array.isArray(payload) &&
      Object.keys(payload).length === 0
    ) {
      return "{}"
    }

    return JSON.stringify(payload, null, 2)
  } catch {
    return "{}"
  }
}

type ParsedTriggerResult =
  | { isValid: false }
  | { isValid: true; payload: Record<string, unknown>; normalized: string }

const parseTriggerPayload = (value: string): ParsedTriggerResult => {
  const trimmed = value.trim()
  if (!trimmed) {
    return { isValid: true, payload: {}, normalized: "{}" }
  }
  const payload = JSON.parse(trimmed) as Record<string, unknown>
  return {
    isValid: true,
    payload,
    normalized: JSON.stringify(payload, null, 2),
  }
}

export function TaskWorkflowStatus({
  task,
  caseId,
  executionId,
  workflow,
  onExecutionStart,
}: TaskWorkflowStatusProps) {
  const { toast } = useToast()
  const workspaceId = useWorkspaceId()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [jsonValue, setJsonValue] = useState<string>(() =>
    formatTriggerData(task.workflow_inputs)
  )
  const [savedJson, setSavedJson] = useState<string>(() =>
    formatTriggerData(task.workflow_inputs)
  )
  const [saveError, setSaveError] = useState<string | null>(null)

  const { createExecution, createExecutionIsPending } =
    useCreateManualWorkflowExecution(task.workflow_id || "")

  const { updateTask, updateTaskIsPending } = useUpdateCaseTask({
    caseId,
    workspaceId,
    taskId: task.id,
  })

  const updateTaskAsync = useCallback(
    (values: CaseTaskUpdate) =>
      new Promise<void>((resolve, reject) => {
        updateTask(values, {
          onSuccess: () => resolve(),
          onError: (error) => reject(error),
        })
      }),
    [updateTask]
  )

  const { execution, executionIsLoading, executionError } =
    useCompactWorkflowExecution(executionId || undefined)

  const encodedId = executionId ? encodeURIComponent(executionId) : ""
  const { execution: fullExecution } = useWorkflowExecution(encodedId, {
    enabled: dialogOpen && execution?.status === "COMPLETED" && !!executionId,
  })

  const prevStatusRef = useRef<string | undefined>()

  const parsedTrigger = useMemo<ParsedTriggerResult>(() => {
    try {
      return parseTriggerPayload(jsonValue)
    } catch {
      return { isValid: false }
    }
  }, [jsonValue])

  const isDirty = useMemo(() => {
    if (!parsedTrigger.isValid) {
      return true
    }
    return parsedTrigger.normalized !== savedJson
  }, [parsedTrigger, savedJson])

  useEffect(() => {
    const formatted = formatTriggerData(task.workflow_inputs)
    setSavedJson(formatted)
    if (!dialogOpen) {
      setJsonValue(formatted)
      setSaveError(null)
    }
  }, [dialogOpen, task.id, task.workflow_inputs])

  const handleDialogOpenChange = useCallback(
    (open: boolean) => {
      if (open) {
        setJsonValue(savedJson)
        setSaveError(null)
      } else {
        setSaveError(null)
      }
      setDialogOpen(open)
    },
    [savedJson]
  )

  const handleJsonChange = useCallback((value: string) => {
    setJsonValue(value)
  }, [])

  const handleSaveTriggerData = useCallback(async () => {
    if (!parsedTrigger.isValid) {
      toast({
        title: "Invalid trigger data",
        description: "Fix the JSON before saving.",
      })
      return
    }

    try {
      await updateTaskAsync({ workflow_inputs: parsedTrigger.payload })
      setSavedJson(parsedTrigger.normalized)
      setJsonValue(parsedTrigger.normalized)
      setSaveError(null)
      toast({
        title: "Trigger data saved",
        description: "Workflow inputs are now stored on this task.",
      })
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "Failed to save trigger data."
      setSaveError(message)
    }
  }, [parsedTrigger, toast, updateTaskAsync])

  const handleRunWorkflow = useCallback(async () => {
    if (!task.workflow_id) {
      return
    }

    if (!parsedTrigger.isValid) {
      toast({
        title: "Invalid trigger data",
        description: "Fix the JSON before running the workflow.",
      })
      return
    }

    try {
      const payload = parsedTrigger.payload
      const inputs =
        payload && Object.keys(payload).length > 0 ? payload : undefined
      const result = await createExecution({
        workflow_id: task.workflow_id,
        inputs,
      })
      onExecutionStart(task.id, result.wf_exec_id)
    } catch (error) {
      const message =
        error instanceof Error
          ? error.message
          : "Failed to start workflow execution."
      toast({
        title: "Failed to start workflow",
        description: message,
      })
    }
  }, [
    createExecution,
    parsedTrigger,
    onExecutionStart,
    task.id,
    task.workflow_id,
    toast,
  ])

  const isRunning =
    createExecutionIsPending ||
    executionIsLoading ||
    execution?.status === "RUNNING"

  const isErrorState =
    (execution?.status && FAILURE_STATUSES.has(execution.status)) ||
    Boolean(executionError && executionId)

  const lastResult = useMemo(() => {
    if (!fullExecution?.events) {
      return null
    }
    const completedWorkflowEvent = fullExecution.events.find(
      (event) => event.event_type === "WORKFLOW_EXECUTION_COMPLETED"
    )
    return completedWorkflowEvent?.result ?? null
  }, [fullExecution?.events])

  const executionStatusLabel = useMemo(() => {
    if (execution?.status) {
      return execution.status.toLowerCase().replace(/_/g, " ")
    }
    if (executionId) {
      return "pending"
    }
    return "not started"
  }, [execution?.status, executionId])

  const hasExecutionData = Boolean(execution)

  const statusIcon = useMemo(() => {
    if (isRunning) {
      return <Loader2 className="h-3 w-3 animate-spin text-blue-500" />
    }
    if (execution?.status === "COMPLETED") {
      return <Check className="h-3 w-3 text-green-600" />
    }
    if (isErrorState) {
      return <XCircle className="h-3 w-3 text-red-500" />
    }
    if (executionId && !hasExecutionData) {
      return <Loader2 className="h-3 w-3 animate-spin text-blue-500" />
    }
    return <PlayCircle className="h-3 w-3" />
  }, [
    execution?.status,
    executionId,
    hasExecutionData,
    isErrorState,
    isRunning,
  ])

  useEffect(() => {
    if (!execution?.status) {
      return
    }
    const prevStatus = prevStatusRef.current
    const currentStatus = execution.status

    if (prevStatus && prevStatus !== currentStatus) {
      if (currentStatus === "COMPLETED") {
        toast({
          title: "Workflow completed",
          description: "The workflow executed successfully.",
        })
      } else if (FAILURE_STATUSES.has(currentStatus)) {
        toast({
          title: "Workflow failed",
          description: `Execution ${currentStatus.toLowerCase()}`,
        })
      }
    }

    prevStatusRef.current = currentStatus
  }, [execution?.status, toast])

  const executionSummary = executionId ? `Execution ${executionId}` : ""
  const lastExecutionTimestamp =
    execution?.status === "COMPLETED"
      ? (fullExecution?.close_time ??
        execution?.close_time ??
        execution?.start_time ??
        "")
      : ""

  return (
    <Dialog open={dialogOpen} onOpenChange={handleDialogOpenChange}>
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <DialogTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0"
                onClick={(event) => {
                  event.stopPropagation()
                }}
              >
                {statusIcon}
              </Button>
            </DialogTrigger>
          </TooltipTrigger>
          <TooltipContent side="top" className="text-xs">
            Configure workflow trigger
          </TooltipContent>
        </Tooltip>
      </TooltipProvider>

      <DialogContent className="max-w-3xl">
        <DialogHeader>
          <DialogTitle className="text-base">{workflow.title}</DialogTitle>
          <DialogDescription className="text-xs text-muted-foreground">
            Customize the trigger data stored on “{task.title}” and run this
            workflow on demand.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4">
          <div className="rounded-md border border-border/70 bg-muted/30 px-3 py-2 text-xs">
            <div className="flex items-center justify-between">
              <div className="font-medium text-foreground">
                Latest status: {executionStatusLabel}
              </div>
              {executionSummary && (
                <span className="text-muted-foreground">
                  {executionSummary}
                </span>
              )}
            </div>
            {isErrorState && (
              <p className="mt-1 text-[11px] text-red-600">
                The previous run ended in an error. Update the trigger data and
                try again.
              </p>
            )}
          </div>

          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <div>
                <p className="text-sm font-medium">Trigger data</p>
                <p className="text-xs text-muted-foreground">
                  JSON payload persisted on this task and sent to the workflow
                  when triggered.
                </p>
              </div>
              {isDirty && parsedTrigger.isValid && (
                <span className="text-[11px] font-medium text-amber-600">
                  Unsaved changes
                </span>
              )}
            </div>
            <div className="h-64 rounded-md border border-border">
              <CodeEditor
                value={jsonValue}
                onChange={handleJsonChange}
                language="json"
                className="h-full"
              />
            </div>
            {!parsedTrigger.isValid && (
              <p className="text-xs">Invalid JSON. Please fix the syntax.</p>
            )}
          </div>

          {saveError && (
            <Alert>
              <AlertCircle className="h-4 w-4" />
              <AlertTitle>Failed to save trigger data</AlertTitle>
              <AlertDescription className="text-xs">
                {saveError}
              </AlertDescription>
            </Alert>
          )}

          {execution?.status === "COMPLETED" && (
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <p className="text-sm font-medium">Last run result</p>
                {lastExecutionTimestamp && (
                  <span className="text-[11px] text-muted-foreground">
                    {lastExecutionTimestamp}
                  </span>
                )}
              </div>
              {lastResult ? (
                <JsonViewWithControls
                  src={lastResult}
                  showControls={false}
                  defaultExpanded
                  defaultTab="nested"
                />
              ) : (
                <p className="text-xs text-muted-foreground">
                  No result payload was returned from the last execution.
                </p>
              )}
            </div>
          )}
        </div>

        <DialogFooter className="gap-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => handleDialogOpenChange(false)}
          >
            Close
          </Button>
          <Button
            type="button"
            variant="secondary"
            onClick={handleSaveTriggerData}
            disabled={!parsedTrigger.isValid || !isDirty || updateTaskIsPending}
          >
            {updateTaskIsPending && (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            )}
            Save trigger data
          </Button>
          <Button
            type="button"
            onClick={handleRunWorkflow}
            disabled={isRunning || !parsedTrigger.isValid}
          >
            {isRunning ? (
              <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="mr-1.5 h-3.5 w-3.5" />
            )}
            Run workflow
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
