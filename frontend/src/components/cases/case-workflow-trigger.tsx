"use client"

import React, { useCallback, useMemo, useState } from "react"
import Link from "next/link"
import { CaseRead, WorkflowReadMinimal } from "@/client"
import { useWorkspace } from "@/providers/workspace"
import { PlayIcon, SquareArrowOutUpRightIcon } from "lucide-react"

import {
  useCreateManualWorkflowExecution,
  useLocalStorage,
  useWorkflowManager,
} from "@/lib/hooks"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Checkbox } from "@/components/ui/checkbox"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { TooltipProvider } from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { JsonViewWithControls } from "@/components/json-viewer"

interface CaseWorkflowTriggerProps {
  caseData: CaseRead
}

/**
 * Renders a workflow trigger section for a case.
 * Allows selecting a workflow and triggering it with the case data as input.
 * @param caseData The data of the current case.
 * @returns JSX.Element
 */
export function CaseWorkflowTrigger({ caseData }: CaseWorkflowTriggerProps) {
  const { workspaceId } = useWorkspace()
  // Get the manual execution hook for the selected workflow (if any)
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(
    null
  )
  // Use the useLocalStorage hook
  const [flattenCaseFields, setFlattenCaseFields] = useLocalStorage(
    "flattenCaseFields",
    false
  )

  const { createExecution, createExecutionIsPending } =
    useCreateManualWorkflowExecution(selectedWorkflowId || "")
  const triggerInputs = useMemo(() => {
    const fields = Object.fromEntries(
      caseData.fields
        .filter((field) => !field.reserved)
        .map((field) => [field.id, field.value])
    )
    if (flattenCaseFields) {
      return {
        case_id: caseData.id,
        ...fields,
      }
    }
    return {
      case_id: caseData.id,
      case_fields: fields,
    }
  }, [caseData, flattenCaseFields])
  const [isConfirmOpen, setIsConfirmOpen] = useState(false)

  const selectedWorkflowUrl = `/workspaces/${workspaceId}/workflows/${selectedWorkflowId}`
  // Fetch workflows
  const { workflows, workflowsLoading, workflowsError } = useWorkflowManager()

  const handleTrigger = useCallback(async () => {
    if (!selectedWorkflowId) return
    await createExecution({
      workflow_id: selectedWorkflowId,
      inputs: triggerInputs,
    })
    toast({
      title: "Workflow run started",
      description: (
        <Link
          href={selectedWorkflowUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          <div className="flex items-center space-x-1">
            <SquareArrowOutUpRightIcon className="size-3" />
            <span>View workflow run</span>
          </div>
        </Link>
      ),
    })
  }, [createExecution, selectedWorkflowId, triggerInputs])

  // Loading state
  if (workflowsLoading) {
    return <Skeleton className="h-10 w-full" />
  }

  // Error state
  if (workflowsError) {
    return (
      <div className="text-xs text-destructive">
        Error loading workflows: {workflowsError.message}
      </div>
    )
  }

  const selectedWorkflow = workflows?.find((wf) => wf.id === selectedWorkflowId)
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center space-x-2">
        <Select
          onValueChange={setSelectedWorkflowId}
          value={selectedWorkflowId || "Select a workflow..."}
        >
          <SelectTrigger className="flex-1 text-xs text-muted-foreground">
            <SelectValue asChild>
              {selectedWorkflow ? (
                <CaseWorkflowTriggerSelectItem workflow={selectedWorkflow} />
              ) : (
                <span className="text-xs text-muted-foreground">
                  Select a workflow...
                </span>
              )}
            </SelectValue>
          </SelectTrigger>
          <SelectContent>
            {workflows && workflows.length > 0 ? (
              workflows.map((workflow) => (
                <SelectItem key={workflow.id} value={workflow.id}>
                  <div className="flex flex-col text-xs">
                    <CaseWorkflowTriggerSelectItem workflow={workflow} />
                    {workflow.description && (
                      <span className="text-xs text-muted-foreground">
                        {workflow.description}
                      </span>
                    )}
                  </div>
                </SelectItem>
              ))
            ) : (
              <div className="p-2 text-center text-xs text-muted-foreground">
                No workflows found.
              </div>
            )}
          </SelectContent>
        </Select>

        <AlertDialog open={isConfirmOpen} onOpenChange={setIsConfirmOpen}>
          <AlertDialogTrigger asChild>
            <Button
              size="sm"
              disabled={!selectedWorkflowId || createExecutionIsPending}
              className="bg-emerald-400 hover:bg-emerald-400/80 hover:text-white"
            >
              <PlayIcon className="size-3 fill-white stroke-white" />
            </Button>
          </AlertDialogTrigger>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Confirm Workflow Trigger</AlertDialogTitle>
              <AlertDialogDescription>
                Are you sure you want to trigger the workflow &quot;
                <span className="font-semibold">{selectedWorkflow?.title}</span>
                &quot; with the following trigger inputs?
                <br />
                <br />
                <TooltipProvider>
                  <JsonViewWithControls
                    src={triggerInputs}
                    showControls={false}
                    defaultTab="nested"
                    defaultExpanded
                  />
                </TooltipProvider>
                <div className="mt-4 flex items-center space-x-2">
                  <Checkbox
                    id="flatten-fields-toggle"
                    checked={flattenCaseFields}
                    onCheckedChange={setFlattenCaseFields}
                  />
                  <Label htmlFor="flatten-fields-toggle" className="text-xs">
                    Pass case fields as top-level keys
                  </Label>
                </div>
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction onClick={handleTrigger}>
                <PlayIcon className="mr-2 size-3 fill-white stroke-white" />
                <span>Trigger</span>
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
      {selectedWorkflowId && (
        <Link
          href={selectedWorkflowUrl}
          target="_blank"
          rel="noopener noreferrer"
        >
          <div className="flex items-center space-x-2 text-xs text-muted-foreground">
            <SquareArrowOutUpRightIcon className="size-3" />
            <span>View workflow</span>
          </div>
        </Link>
      )}
    </div>
  )
}

function CaseWorkflowTriggerSelectItem({
  workflow,
}: {
  workflow: WorkflowReadMinimal
}) {
  return (
    <div className="flex items-center space-x-2">
      <span>{workflow.title}</span>
      {workflow.alias && (
        <Badge variant="secondary" className="ml-2 text-xs font-normal">
          {workflow.alias}
        </Badge>
      )}
    </div>
  )
}
