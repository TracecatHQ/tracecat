"use client"

import { useParams } from "next/navigation"
import type React from "react"
import { useState } from "react"
import { ControlsHeader } from "@/components/nav/controls-header"
import { RunbookChat } from "@/components/runbooks/runbook-chat"
import { AppSidebar } from "@/components/sidebar/app-sidebar"
import { ResizableSidebar } from "@/components/ui/resizable-sidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"

export default function RunbookDetailLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const params = useParams<{ runbookId: string }>()
  const runbookId = params?.runbookId

  // Track whether the chat sidebar is open
  const [chatOpen, setChatOpen] = useState(true)

  if (!runbookId) {
    return <>{children}</>
  }

  return (
    <SidebarProvider>
      <AppSidebar />
      {/* Runbook content inset */}
      <SidebarInset className="flex-1 min-w-0 mr-px">
        <div className="flex h-full flex-col">
          <ControlsHeader
            isChatOpen={chatOpen}
            onToggleChat={() => setChatOpen((prev) => !prev)}
          />
          <div className="flex-1 overflow-y-auto">{children}</div>
        </div>
      </SidebarInset>

      {/* Chat sidebar */}
      {chatOpen && (
        <ResizableSidebar>
          <RunbookChat runbookId={runbookId} isChatOpen />
        </ResizableSidebar>
      )}
    </SidebarProvider>
  )
}
