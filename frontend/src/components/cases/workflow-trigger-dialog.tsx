"use client"

import { useQuery } from "@tanstack/react-query"
import { ArrowUpRight, PlayIcon } from "lucide-react"
import Link from "next/link"
import { useCallback, useMemo } from "react"
import type { ApiError, CaseRead, WorkflowRead } from "@/client"
import { workflowsGetWorkflow } from "@/client"
import {
  type TriggerFormValues,
  WorkflowTriggerForm,
} from "@/components/cases/workflow-trigger-form"
import { JsonViewWithControls } from "@/components/json-viewer"
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Switch } from "@/components/ui/switch"
import { TooltipProvider } from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { useWorkflowTriggerInputs } from "@/hooks/use-workflow-trigger-inputs"
import { useCreateManualWorkflowExecution } from "@/lib/hooks"
import type { TracecatJsonSchema } from "@/lib/schema"
import { useWorkspaceId } from "@/providers/workspace-id"

type WorkflowWithSchema = WorkflowRead & {
  expects_schema?: TracecatJsonSchema | null
}

interface WorkflowTriggerDialogProps {
  caseData: CaseRead
  workflowId: string | null
  workflowTitle?: string | null
  defaultTriggerValues?: Record<string, unknown> | null
  taskId?: string
  open: boolean
  onOpenChange: (open: boolean) => void
}

export function WorkflowTriggerDialog({
  caseData,
  workflowId,
  workflowTitle,
  defaultTriggerValues,
  taskId,
  open,
  onOpenChange,
}: WorkflowTriggerDialogProps) {
  const workspaceId = useWorkspaceId()

  const { createExecution, createExecutionIsPending } =
    useCreateManualWorkflowExecution(
      workflowId || "",
      taskId ? { caseId: caseData.id, taskId } : undefined
    )

  const {
    caseFieldsRecord,
    fallbackInputs,
    groupCaseFields,
    setGroupCaseFields,
  } = useWorkflowTriggerInputs(caseData, taskId)

  const { data: selectedWorkflowDetail } = useQuery<
    WorkflowWithSchema | null,
    ApiError
  >({
    enabled: Boolean(workspaceId && workflowId),
    queryKey: ["workflow-detail", workflowId],
    queryFn: async ({ queryKey }) => {
      if (!workspaceId) {
        return null
      }
      const queriedWorkflowId = queryKey[1] as string | null
      if (!queriedWorkflowId) {
        return null
      }
      const workflowDetail = await workflowsGetWorkflow({
        workspaceId,
        workflowId: queriedWorkflowId,
      })
      return workflowDetail as WorkflowWithSchema
    },
  })

  const triggerSchema = useMemo<TracecatJsonSchema | null>(() => {
    const schema = selectedWorkflowDetail?.expects_schema
    if (!schema || typeof schema !== "object" || Array.isArray(schema)) {
      return null
    }
    if ("type" in schema && schema.type !== "object") {
      return null
    }
    return schema as TracecatJsonSchema
  }, [selectedWorkflowDetail])

  const effectiveGroupCaseFields = triggerSchema ? false : groupCaseFields

  const selectedWorkflowUrl =
    workspaceId && workflowId
      ? `/workspaces/${workspaceId}/workflows/${workflowId}`
      : null

  const selectedWorkflowName =
    selectedWorkflowDetail?.title ?? workflowTitle ?? "this workflow"

  const showExecutionStartedToast = useCallback(() => {
    if (!workflowId || !selectedWorkflowUrl) {
      return
    }
    toast({
      title: "Workflow run started",
      description: (
        <Link
          href={selectedWorkflowUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          <div className="flex items-center space-x-1">
            <ArrowUpRight className="size-3" />
            <span>View workflow run</span>
          </div>
        </Link>
      ),
    })
  }, [selectedWorkflowUrl, workflowId])

  const handleSchemaSubmit = useCallback(
    async (values: TriggerFormValues) => {
      if (!workflowId) return
      await createExecution({
        workflow_id: workflowId,
        inputs: values,
      })
      showExecutionStartedToast()
      onOpenChange(false)
    },
    [createExecution, onOpenChange, showExecutionStartedToast, workflowId]
  )

  const handleTriggerWithoutSchema = useCallback(async () => {
    if (!workflowId) return
    await createExecution({
      workflow_id: workflowId,
      inputs: fallbackInputs,
    })
    showExecutionStartedToast()
    onOpenChange(false)
  }, [
    createExecution,
    fallbackInputs,
    onOpenChange,
    showExecutionStartedToast,
    workflowId,
  ])

  if (!workflowId) {
    return null
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent className="max-w-md">
        <AlertDialogHeader>
          <AlertDialogTitle className="text-sm">
            {triggerSchema
              ? "Configure workflow inputs"
              : "Confirm workflow trigger"}
          </AlertDialogTitle>
          <AlertDialogDescription className="text-xs">
            {triggerSchema
              ? `Provide the inputs required by "${selectedWorkflowName}". Defaults are populated from the case where possible.`
              : `Are you sure you want to trigger "${selectedWorkflowName}" with the following inputs?`}
          </AlertDialogDescription>
        </AlertDialogHeader>
        {triggerSchema ? (
          <WorkflowTriggerForm
            schema={triggerSchema}
            caseId={caseData.id}
            caseFields={caseFieldsRecord}
            groupCaseFields={effectiveGroupCaseFields}
            defaultTriggerValues={defaultTriggerValues}
            taskId={taskId}
            onSubmit={handleSchemaSubmit}
            isSubmitting={createExecutionIsPending}
          />
        ) : (
          <>
            <div className="mt-4 space-y-3">
              <div className="flex items-center justify-between gap-3 rounded-md border border-border bg-muted/40 px-3 py-2">
                <div className="space-y-1 text-xs">
                  <div className="font-medium">Group case fields</div>
                  <p className="text-[11px] text-muted-foreground">
                    Send case data under a single <code>case_fields</code>{" "}
                    object.
                  </p>
                </div>
                <Switch
                  checked={groupCaseFields}
                  onCheckedChange={(value) => setGroupCaseFields(value)}
                  className="h-4 w-8"
                />
              </div>
              <TooltipProvider>
                <JsonViewWithControls
                  src={fallbackInputs}
                  showControls={false}
                  defaultTab="nested"
                  defaultExpanded
                />
              </TooltipProvider>
            </div>
            <AlertDialogFooter>
              <AlertDialogCancel className="text-xs">Cancel</AlertDialogCancel>
              <Button
                type="button"
                onClick={handleTriggerWithoutSchema}
                className="text-xs"
                disabled={createExecutionIsPending}
              >
                <PlayIcon className="mr-1.5 h-3 w-3" />
                Trigger
              </Button>
            </AlertDialogFooter>
          </>
        )}
      </AlertDialogContent>
    </AlertDialog>
  )
}
