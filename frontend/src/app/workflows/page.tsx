"use client"

import React from "react"
import Link from "next/link"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"

interface Workflow {
  id: string
  title: string
  description: string
  status: string
}

export default function PlaceholderWorkflowsPage() {
  const [workflows, setWorkflows] = React.useState<Workflow[]>([])

  React.useEffect(() => {
    async function fetchWorkflows() {
      const response = await fetch(`http://localhost:8000/workflows`)
      const data = await response.json()
      setWorkflows(data)
    }
    fetchWorkflows()
  }, [])
  return (
    <div className="flex h-full w-full items-center justify-center">
      {workflows.length > 0 &&
        workflows.map((workflow, idx) => (
          <Button asChild className="h-[50px]">
            <Link
              key={idx}
              href={`/workflows/${workflow.id}`}
              className={cn(
                "dark:bg-muted dark:text-white dark:hover:bg-muted dark:hover:text-white",
                "m-4 flex flex-col justify-start"
              )}
            >
              {workflow.title}
              <span className="ml-2">{workflow.id}</span>
            </Link>
          </Button>
        ))}
    </div>
  )
}
