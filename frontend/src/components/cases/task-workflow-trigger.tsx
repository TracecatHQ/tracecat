"use client"

import { PlayIcon } from "lucide-react"
import { useState } from "react"
import type { CaseRead, CaseTaskRead, WorkflowReadMinimal } from "@/client"
import { WorkflowTriggerDialog } from "@/components/cases/workflow-trigger-dialog"
import { Button } from "@/components/ui/button"

interface TaskWorkflowTriggerProps {
  caseData: CaseRead
  workflow: WorkflowReadMinimal
  task?: CaseTaskRead | null
}

export function TaskWorkflowTrigger({
  caseData,
  workflow,
  task,
}: TaskWorkflowTriggerProps) {
  const [isDialogOpen, setIsDialogOpen] = useState(false)
  const { id: workflowId, title: workflowTitle } = workflow

  if (!caseData) {
    return null
  }

  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        className="h-6 w-6 p-0"
        onClick={(event) => {
          event.stopPropagation()
          setIsDialogOpen(true)
        }}
      >
        <PlayIcon className="h-3 w-3" />
      </Button>
      <WorkflowTriggerDialog
        caseData={caseData}
        workflowId={workflowId}
        workflowTitle={workflowTitle}
        taskId={task?.id}
        defaultTriggerValues={task?.default_trigger_values}
        open={isDialogOpen}
        onOpenChange={setIsDialogOpen}
      />
    </>
  )
}
