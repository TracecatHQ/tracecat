import { Metadata } from "next"
import { EventFeedProvider } from "@/providers/event-feed-stream"
import { WorkflowProvider } from "@/providers/workflow"
import { createClient } from "@/utils/supabase/server"

import Navbar from "@/components/nav/navbar"

export const metadata: Metadata = {
  title: "Workflows | Tracecat",
}

export default async function WorkflowsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const supabase = createClient()

  const {
    data: { session },
  } = await supabase.auth.getSession()
  return (
    <WorkflowProvider session={session}>
      <EventFeedProvider>
        <div className="no-scrollbar flex h-screen max-h-screen flex-col">
          <Navbar session={session} />
          {children}
        </div>
      </EventFeedProvider>
    </WorkflowProvider>
  )
}
