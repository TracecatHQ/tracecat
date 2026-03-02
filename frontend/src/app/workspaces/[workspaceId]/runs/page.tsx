"use client"

import { useEffect } from "react"
import { WorkflowRunsLayout } from "@/components/workflow-runs/workflow-runs-layout"

export default function WorkflowRunsPage() {
  useEffect(() => {
    if (typeof window !== "undefined") {
      document.title = "Runs"
    }
  }, [])

  return <WorkflowRunsLayout />
}
