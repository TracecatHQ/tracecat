import { type Metadata } from "next"
import { WorkflowProvider } from "@/providers/workflow"

import Navbar from "@/components/nav/navbar"

export const metadata: Metadata = {
  title: "Playbooks",
  description: "Pre-built workflows ready to deploy.",
}

export default async function WorkflowsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <WorkflowProvider>
      <div className="no-scrollbar flex h-screen max-h-screen flex-col">
        <Navbar />
        {children}
      </div>
    </WorkflowProvider>
  )
}
