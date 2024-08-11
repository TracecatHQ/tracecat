import { type Metadata } from "next"
import { WorkflowProvider } from "@/providers/workflow"

import { DynamicNavbar } from "@/components/nav/dynamic-nav"

export const metadata: Metadata = {
  title: "Workflows",
}

export default async function WorkspaceLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <WorkflowProvider>
      <div className="no-scrollbar flex h-screen max-h-screen flex-col">
        {/* DynamicNavbar needs a WorkflowProvider */}
        <DynamicNavbar />
        {children}
      </div>
    </WorkflowProvider>
  )
}
