"use client"

import { useParams } from "next/navigation"
import type React from "react"
import { useState } from "react"
import { CaseChat } from "@/components/cases/case-chat"
import { ControlsHeader } from "@/components/nav/controls-header"
import { AppSidebar } from "@/components/sidebar/app-sidebar"
import { ResizableSidebar } from "@/components/ui/resizable-sidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"

export default function CaseDetailLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const params = useParams<{ caseId: string }>()
  const caseId = params?.caseId

  // Track whether the chat sidebar is open
  const [chatOpen, setChatOpen] = useState(true)

  if (!caseId) {
    return <>{children}</>
  }

  return (
    <SidebarProvider defaultOpen={false}>
      <AppSidebar />
      {/* Case content inset */}
      <SidebarInset className="flex-1 min-w-0 mr-px">
        <div className="flex h-full flex-col">
          <ControlsHeader onToggleChat={() => setChatOpen((prev) => !prev)} />
          <div className="flex-1 overflow-y-auto">{children}</div>
        </div>
      </SidebarInset>

      {/* Chat sidebar */}
      {chatOpen && (
        <ResizableSidebar>
          <CaseChat caseId={caseId} />
        </ResizableSidebar>
      )}
    </SidebarProvider>
  )
}
