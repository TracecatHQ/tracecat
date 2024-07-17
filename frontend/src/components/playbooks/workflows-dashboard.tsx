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
          </div>
          <Button variant="outline" role="combobox" className="ml-auto">
            <Link
              key="public-playbooks"
              target="_blank"
              href="https://github.com/TracecatHQ/tracecat/tree/main/playbooks"
              className="flex items-center space-x-2"
            >
              <InfoIcon className="size-4 text-emerald-600" />
              <span>View public playbooks</span>
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
            <p className="text-sm">No playbooks installed 🗂️</p>
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
