import { Suspense } from "react"
import { type Session } from "@supabase/supabase-js"
import { PlusCircle } from "lucide-react"

import { fetchAllWorkflows } from "@/lib/flow"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { WorkflowItem } from "@/components/dashboard/workflows-dashboard-item"
import {
  NewWorkflowDialog,
  NewWorkflowDialogTrigger,
} from "@/components/new-workflow-dialog"

interface WorkflowsDashboardProps extends React.HTMLAttributes<HTMLElement> {
  session: Session
}

export async function WorkflowsDashboard({ session }: WorkflowsDashboardProps) {
  return (
    <div className="h-full w-full overflow-auto">
      <div className="container flex h-full max-w-[800px] flex-col  space-y-4 p-16">
        <div className="flex w-full pt-16">
          <div className="items-start space-y-2 text-left">
            <h2 className="text-2xl font-bold tracking-tight">Workflows</h2>
            <p className="text-md text-muted-foreground">
              Welcome back! Here&apos;s a list of your workflows.
            </p>
          </div>
          <NewWorkflowDialog>
            <NewWorkflowDialogTrigger asChild>
              <Button
                variant="outline"
                role="combobox"
                className="ml-auto space-x-2"
              >
                <PlusCircle className="mr-2 h-4 w-4" />
                New
              </Button>
            </NewWorkflowDialogTrigger>
          </NewWorkflowDialog>
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
          <WorkflowList session={session} />
        </Suspense>
      </div>
    </div>
  )
}

interface WorkflowListProps {
  session: Session
}

export async function WorkflowList({ session }: WorkflowListProps) {
  const workflows = await fetchAllWorkflows(session)
  return (
    <div className="flex flex-col gap-2 pt-4">
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
