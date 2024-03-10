"use client"

import React, { useEffect, useState } from "react"
import Link from "next/link"
import { useSessionContext } from "@/providers/session"
import { useQuery } from "@tanstack/react-query"
import { Loader2, PlusCircle } from "lucide-react"

import { WorkflowMetadata } from "@/types/schemas"
import { fetchWorkflows } from "@/lib/flow"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  NewWorkflowDialog,
  NewWorkflowDialogTrigger,
} from "@/components/new-workflow-dialog"
import NoSSR from "@/components/no-ssr"

export default function Page() {
  return (
    <NoSSR>
      <WorkflowsPage suppressHydrationWarning />
    </NoSSR>
  )
}
function WorkflowsPage(props: React.HTMLAttributes<HTMLElement>) {
  const { session, isLoading: sessionIsLoading } = useSessionContext()
  if (!session) {
    return (
      <div
        className="container flex h-full w-full items-center justify-center"
        {...props}
      >
        <Link href="/login">Go to Login</Link>
      </div>
    )
  }
  const { user } = session

  if (sessionIsLoading || !user) {
    return (
      <div
        className="container flex h-full w-full items-center justify-center"
        {...props}
      >
        <Loader2 className="h-6 w-6 animate-spin" color="#8c8c8c" />
      </div>
    )
  }
  const {
    data: userWorkflows,
    isLoading,
    error,
  } = useQuery<WorkflowMetadata[], Error>({
    queryKey: ["workflows"],
    queryFn: fetchWorkflows,
  })
  return (
    <div className="container flex h-full max-w-[800px] flex-col justify-center space-y-2 p-16">
      <div className="flex w-full ">
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
              onClick={() => {
                console.log("Create new workflow")
              }}
            >
              <PlusCircle className="mr-2 h-4 w-4" />
              New
            </Button>
          </NewWorkflowDialogTrigger>
        </NewWorkflowDialog>
      </div>
      {!isLoading && <WorkflowList workflows={userWorkflows ?? []} />}
    </div>
  )
}

interface WorkflowListProps {
  workflows: WorkflowMetadata[]
}

export function WorkflowList({ workflows }: WorkflowListProps) {
  return (
    <div className="flex flex-col gap-2 pt-0">
      {workflows.length === 0 ? (
        <span>No workflows created.</span>
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
                    <span
                      className={cn(
                        "flex h-2 w-2 rounded-full ",
                        wf.status === "online" ? "bg-green-400" : "bg-gray-400"
                      )}
                    />
                    <span className="text-xs text-muted-foreground">
                      Last run: 2 hours ago
                    </span>
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
