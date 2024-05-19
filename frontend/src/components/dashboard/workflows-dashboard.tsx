import { Suspense } from "react";
import { Skeleton } from "@/components/ui/skeleton";

import { fetchAllWorkflows } from "@/lib/flow";
import { WorkflowItem } from "@/components/dashboard/workflows-dashboard-item";
import CreateWorkflowButton from "@/components/dashboard/create-workflow-button"; // Ensure the correct path

export async function WorkflowsDashboard() {
  return (
    <div className="h-full w-full overflow-auto">
      <div className="container flex h-full max-w-[800px] flex-col space-y-12 pt-32 p-16">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-bold tracking-tight">Workflows</h2>
            <p className="text-md text-muted-foreground">
              Your workflows dashboard.
            </p>
          </div>
          <CreateWorkflowButton />
        </div>
        <Suspense
          fallback={
            <div className="flex flex-col gap-2 pt-4">
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-24 w-full" />
              <Skeleton className="h-24 w-full" />
            </div>
          }
        >
          <WorkflowList />
        </Suspense>
      </div>
    </div>
  );
}

export async function WorkflowList() {
  const workflows = await fetchAllWorkflows()
  return (
    <div className="flex flex-col space-y-4">
      {workflows.length === 0 ? (
        <span className="my-4 text-center text-sm text-muted-foreground">
          No workflows created.
        </span>
      ) : (
        <>
          {workflows.map((wf, idx) => (
            <WorkflowItem key={idx} workflow={wf} />
          ))}
        </>
      )}
    </div>
  )
}
