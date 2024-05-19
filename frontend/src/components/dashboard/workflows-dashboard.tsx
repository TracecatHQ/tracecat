import { Suspense } from "react";
import { Skeleton } from "@/components/ui/skeleton";

import Link from "next/link";
import { ConeIcon } from "lucide-react";
import { fetchAllWorkflows } from "@/lib/flow";
import { Button } from "@/components/ui/button";
import { WorkflowItem } from "@/components/dashboard/workflows-dashboard-item";
import CreateWorkflowButton from "@/components/dashboard/create-workflow-button";

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
          <div className="ml-auto space-x-2">
            <CreateWorkflowButton />
            <Link href="/playbooks">
              <Button
                variant="outline"
                role="combobox"
                className="space-x-2"
              >
                <ConeIcon className="h-4 w-4 text-lime-600" />
                <span>Find playbook</span>
              </Button>
            </Link>
          </div>
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
        <div className="flex flex-col items-center w-full space-y-12">
          <div className="flex items-center space-x-4 w-full justify-center">
            <Skeleton className="h-12 w-12 rounded-full" />
            <div className="space-y-2">
              <Skeleton className="h-4 w-[250px]" />
              <Skeleton className="h-4 w-[200px]" />
            </div>
          </div>
          <div className="flex items-center space-x-4 w-full justify-center">
            <Skeleton className="h-12 w-12 rounded-full" />
            <div className="space-y-2">
              <Skeleton className="h-4 w-[250px]" />
              <Skeleton className="h-4 w-[200px]" />
            </div>
          </div>
          <div className="text-center space-y-4">
            <p className="text-sm">
              Welcome to Tracecat 👋
            </p>
            <p className="text-center text-xs text-muted-foreground max-w-lg">
              The modern security automation platform designed to reduce noise.
            </p>
          </div>
        </div>
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
