"use client"

import { useParams } from "next/navigation"
import type React from "react"
import { CaseChat } from "@/components/cases/case-chat"
import { ControlsHeader } from "@/components/nav/controls-header"
import { AppSidebar } from "@/components/sidebar/app-sidebar"
import { ResizableSidebar } from "@/components/ui/resizable-sidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { useAutoCollapseSidebar } from "@/hooks/use-auto-collapse-sidebar"
import { useWorkspaceChatOpen } from "@/hooks/use-workspace-chat-open"

/**
 * Collapses the left nav on narrow viewports. A component rather than a
 * direct hook call because it needs the sidebar context, which only exists
 * inside `SidebarProvider`.
 */
function AutoCollapseSidebar() {
  useAutoCollapseSidebar()
  return null
}

export default function CaseDetailLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const params = useParams<{ caseId: string }>()
  const caseId = params?.caseId

  const [chatOpen, setChatOpen] = useWorkspaceChatOpen()

  if (!caseId) {
    return <>{children}</>
  }

  return (
    <SidebarProvider>
      <AutoCollapseSidebar />
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
