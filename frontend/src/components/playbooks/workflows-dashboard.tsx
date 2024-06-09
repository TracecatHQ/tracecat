import { Suspense } from "react"
import Link from "next/link"
import { InfoIcon } from "lucide-react"

import { fetchAllPlaybooks } from "@/lib/workflow"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { WorkflowItem } from "@/components/dashboard/workflows-dashboard-item"

export async function WorkflowsDashboard() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[800px] flex-col space-y-12 p-16 pt-32">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-bold tracking-tight">Playbooks</h2>
            <p className="text-md text-muted-foreground">
              Automate SecOps with production-ready playbooks.
            </p>
          </div>
          <Button variant="outline" role="combobox" className="ml-auto">
            <Link
              key="book-a-demo"
              target="_blank"
              href="https://calendly.com/meet-tracecat/super-quick-intro"
              className="flex items-center space-x-2"
            >
              <InfoIcon className="size-4 text-emerald-600" />
              <span>Book demo</span>
            </Link>
          </Button>
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
  )
}

export async function WorkflowList() {
  const workflows = await fetchAllPlaybooks()
  return (
    <div className="flex flex-col space-y-4">
      {workflows.length === 0 ? (
        <div className="flex w-full flex-col items-center space-y-12">
          <div className="flex w-full items-center justify-center space-x-4">
            <Skeleton className="size-12 rounded-full" />
            <div className="space-y-2">
              <Skeleton className="h-4 w-[250px]" />
              <Skeleton className="h-4 w-[200px]" />
            </div>
          </div>
          <div className="flex w-full items-center justify-center space-x-4">
            <Skeleton className="size-12 rounded-full" />
            <div className="space-y-2">
              <Skeleton className="h-4 w-[250px]" />
              <Skeleton className="h-4 w-[200px]" />
            </div>
          </div>
          <div className="space-y-4 text-center">
            <p className="text-sm">No playbooks installed 😿</p>
            <p className="max-w-lg text-center text-xs text-muted-foreground">
              Official playbooks are available for verified users only. Please
              request access by booking a demo or sign-up for Tracecat Cloud.
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
