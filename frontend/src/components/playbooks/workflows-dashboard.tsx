"use client"

import Link from "next/link"
import { InfoIcon } from "lucide-react"

import { usePlaybooks } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import { AlertNotification } from "@/components/notifications"
import { WorkflowItem } from "@/components/playbooks/workflows-dashboard-item"
import { ListItemSkeletion } from "@/components/skeletons"

export function PlaybooksDashboard() {
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
              href="https://cal.com/team/tracecat/hello"
              className="flex items-center space-x-2"
            >
              <InfoIcon className="size-4 text-emerald-600" />
              <span>Book demo</span>
            </Link>
          </Button>
        </div>
        <PlaybookList />
      </div>
    </div>
  )
}

export function PlaybookList() {
  const { data: playbooks, error, isLoading } = usePlaybooks()
  if (isLoading) {
    return (
      <div className="flex w-full flex-col items-center space-y-12">
        <ListItemSkeletion n={2} />
      </div>
    )
  }
  if (error || playbooks === undefined) {
    return (
      <AlertNotification level="error" message="Error fetching playbooks" />
    )
  }
  return (
    <div className="flex flex-col space-y-4">
      {playbooks.length === 0 ? (
        <div className="flex w-full flex-col items-center space-y-12">
          <ListItemSkeletion n={2} />
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
          {playbooks.map((wf, idx) => (
            <WorkflowItem key={idx} workflow={wf} />
          ))}
        </>
      )}
    </div>
  )
}
