import { type Metadata } from "next"

import { DynamicNavbar } from "@/components/nav/dynamic-nav"

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
    <div className="no-scrollbar flex h-screen max-h-screen flex-col">
      <DynamicNavbar />
      {children}
    </div>
  )
}
