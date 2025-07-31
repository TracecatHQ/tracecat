"use client"

import { useParams } from "next/navigation"
import type React from "react"
import { useState } from "react"
import { CaseChat } from "@/components/cases/case-chat"
import { ControlsHeader } from "@/components/nav/controls-header"
import { AppSidebar } from "@/components/sidebar/app-sidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"

export default function CaseDetailLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const params = useParams<{ caseId: string }>()
  const caseId = params?.caseId
  const [isChatOpen, setIsChatOpen] = useState(true)

  if (!caseId) {
    return <>{children}</>
  }

  return (
    <SidebarProvider>
      <AppSidebar />
      {/* Case content inset */}
      <SidebarInset>
        <div className="flex h-full flex-col">
          <ControlsHeader />
          <div className="flex-1 overflow-y-auto">{children}</div>
        </div>
      </SidebarInset>

      {/* Chat inset */}
      <SidebarInset className="w-96">
        <CaseChat caseId={caseId} isChatOpen={isChatOpen} />
      </SidebarInset>
    </SidebarProvider>
  )
}
