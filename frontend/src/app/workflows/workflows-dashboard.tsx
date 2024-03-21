import { Suspense } from "react"
import Link from "next/link"
import { type Session } from "@supabase/supabase-js"
import { PlusCircle } from "lucide-react"

import { fetchAllWorkflows } from "@/lib/flow"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
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
          {workflows.map((wf) => (
            <Link
              key={wf.id}
              href={`/workflows/${wf.id}`}
              className={cn(
                "flex min-h-24 min-w-[600px] flex-col items-start justify-start rounded-lg border p-6 text-left text-sm shadow-md transition-all hover:bg-accent",
                "dark:bg-muted dark:text-white dark:hover:bg-muted dark:hover:text-white"
              )}
            >
              <div className="flex w-full flex-col gap-1">
                <div className="flex items-center">
                  <div className="flex items-center gap-2">
                    <div className="font-semibold capitalize">{wf.title}</div>
                  </div>
                  <div className="ml-auto flex items-center space-x-2">
                    <span className="text-xs capitalize text-muted-foreground">
                      {wf.status}
                    </span>
                    <span
                      className={cn(
                        "flex h-2 w-2 rounded-full",
                        wf.status === "online" ? "bg-green-400" : "bg-gray-400"
                      )}
                    />
                  </div>
                </div>
                <div className="text-xs font-medium text-muted-foreground">
                  {wf.description ?? ""}
                </div>
              </div>
            </Link>
          ))}
        </>
      )}
    </div>
  )
}
