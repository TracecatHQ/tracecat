import { type Metadata } from "next"
import { EventFeedProvider } from "@/providers/event-feed-stream"
import { WorkflowProvider } from "@/providers/workflow"

import Navbar from "@/components/nav/navbar"

export const metadata: Metadata = {
  title: "Workflows",
}

export default async function WorkflowsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <WorkflowProvider>
      <EventFeedProvider>
        <div className="no-scrollbar flex h-screen max-h-screen flex-col">
          <Navbar />
          {children}
        </div>
      </EventFeedProvider>
    </WorkflowProvider>
  )
}
