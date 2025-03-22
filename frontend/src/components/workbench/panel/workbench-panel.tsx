import React, { useEffect } from "react"
import { WorkflowRead } from "@/client"
import { useWorkflowBuilder } from "@/providers/builder"
import { useWorkflow } from "@/providers/workflow"
import { Node } from "@xyflow/react"
import { Search } from "lucide-react"

import { FormLoading } from "@/components/loading/form"
import { AlertNotification } from "@/components/notifications"
import {
  ActionPanel,
  ActionPanelRef,
} from "@/components/workbench/panel/action-panel"
import { TriggerPanel } from "@/components/workbench/panel/trigger-panel"
import { WorkflowPanel } from "@/components/workbench/panel/workflow-panel"

export const WorkbenchPanel = React.forwardRef<ActionPanelRef, object>(() => {
  const { selectedNodeId, getNode } = useWorkflowBuilder()
  const { workflow, isLoading, error } = useWorkflow()
  const selectedNode = getNode(selectedNodeId ?? "")

  useEffect(() => {
    if (workflow) {
      document.title = `${workflow.title} | Tracecat`
    }
  }, [workflow])

  if (isLoading || !workflow) {
    return <FormLoading />
  }
  if (error) {
    return (
      <div className="flex size-full items-center justify-center">
        <AlertNotification level="error" message={error.message} />
      </div>
    )
  }
  if (selectedNodeId && !selectedNode) {
    return (
      <div className="flex size-full flex-col items-center justify-center">
        <div className="flex max-w-[50%] flex-col items-center gap-6 p-8 text-center">
          <div className="rounded-full bg-muted/80 p-4">
            <Search className="size-8 text-muted-foreground/80" />
          </div>
          <div className="flex flex-col space-y-3">
            <h4 className="text-base font-semibold tracking-tight">
              Action not found
            </h4>
            <code className="rounded bg-muted px-0 py-1 font-mono text-sm tracking-tighter text-muted-foreground">
              {selectedNodeId}
            </code>
            <p className="text-sm leading-relaxed text-muted-foreground">
              This can happen if you&apos;ve deleted the action or renamed it
              without saving the workflow.
            </p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="size-full overflow-auto">
      {selectedNode ? (
        <NodePanel node={selectedNode} workflow={workflow} />
      ) : (
        <WorkflowPanel workflow={workflow} />
      )}
    </div>
  )
})

WorkbenchPanel.displayName = "WorkbenchPanel"

function NodePanel({ node, workflow }: { node: Node; workflow: WorkflowRead }) {
  switch (node.type) {
    case "udf":
      return <ActionPanel actionId={node.id} workflowId={workflow.id} />
    case "trigger":
      return <TriggerPanel workflow={workflow} />
    case "selector":
      // XXX: Unreachable, as we never select the selector node
      return <></>
    default:
      return <AlertNotification level="error" message="Unknown node type" />
  }
}
