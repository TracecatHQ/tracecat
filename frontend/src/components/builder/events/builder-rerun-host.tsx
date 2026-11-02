"use client"

import { useResolvedLastExecution } from "@/components/builder/events/events-shared"
import { WorkflowRerunDialog } from "@/components/builder/events/workflow-rerun-dialog"
import { executionId as parseExecutionId } from "@/lib/event-history"
import { useWorkflowBuilder } from "@/providers/builder"

/** Rerun dialog for the run shown in the builder, opened from the canvas. */
export function BuilderRerunHost() {
  const {
    rerunRequest,
    setRerunRequest,
    setCurrentExecutionId,
    expandSidebarAndFocusEvents,
  } = useWorkflowBuilder()
  const resolved = useResolvedLastExecution()
  if (resolved.status !== "ready") {
    return null
  }
  return (
    <WorkflowRerunDialog
      executionId={resolved.executionId}
      workflowId={parseExecutionId(resolved.executionId).wf}
      open={rerunRequest !== null}
      onOpenChange={(open) => {
        if (!open) setRerunRequest(null)
      }}
      rerunFromRef={rerunRequest?.fromRef}
      onStarted={(wfExecId) => {
        setCurrentExecutionId(wfExecId)
        expandSidebarAndFocusEvents()
      }}
    />
  )
}
