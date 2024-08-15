"use client"

import Link from "next/link"
import { useWorkspace } from "@/providers/workspace"
import { ConeIcon } from "lucide-react"

import { useWorkflows } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import { CreateWorkflowButton } from "@/components/dashboard/create-workflow-button"
import { WorkflowItem } from "@/components/dashboard/workflows-dashboard-item"
import { AlertNotification } from "@/components/notifications"
import { ListItemSkeletion } from "@/components/skeletons"

export function WorkflowsDashboard() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[800px] flex-col space-y-12 p-16 pt-32">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-bold tracking-tight">Workflows</h2>
            <p className="text-md text-muted-foreground">
              Your workflows dashboard.
            </p>
          </div>
          <div className="ml-auto flex items-center space-x-2">
            <Link href="/playbooks">
              <Button variant="outline" role="combobox" className="space-x-2">
                <ConeIcon className="size-4 text-emerald-600" />
                <span>Find playbook</span>
              </Button>
            </Link>
            <CreateWorkflowButton />
          </div>
        </div>
        <WorkflowList />
      </div>
    </div>
  )
}

export function WorkflowList() {
  const { workspaceId } = useWorkspace()
  const { workflows, workflowsLoading, workflowsError } = useWorkflows()
  if (workflowsLoading) {
    return (
      <div className="flex w-full flex-col items-center space-y-12">
        <ListItemSkeletion n={2} />
      </div>
    )
  }

  if (workflowsError) {
    throw workflowsError
  }

  if (!workflows) {
    return (
      <AlertNotification
        level="error"
        message="Couldn't fetch workflows. Please try again."
      />
    )
  }

  return (
    <div className="flex flex-col space-y-4">
      {workflows.length === 0 ? (
        <div className="flex w-full flex-col items-center space-y-12">
          <ListItemSkeletion n={2} />
          <div className="space-y-4 text-center">
            <p className="text-sm">Welcome to Tracecat 👋</p>
          </div>
        </div>
      ) : (
        <>
          {workflows.map((wf, idx) => (
            <WorkflowItem key={idx} workspaceId={workspaceId} workflow={wf} />
          ))}
        </>
      )}
    </div>
  )
}
