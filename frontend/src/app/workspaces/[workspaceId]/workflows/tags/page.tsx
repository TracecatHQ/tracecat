import type { Metadata } from "next"
import { WorkflowTagsView } from "@/components/dashboard/workflow-tags-view"

export const metadata: Metadata = {
  title: "Workflow tags",
}

export default function WorkflowTagsPage() {
  return <WorkflowTagsView />
}
