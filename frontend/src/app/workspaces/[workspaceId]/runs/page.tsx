import type { Metadata } from "next"
import { WorkflowRunsLayout } from "@/components/workflow-runs/workflow-runs-layout"

export const metadata: Metadata = {
  title: "Runs",
}

export default function WorkflowRunsPage() {
  return <WorkflowRunsLayout />
}
