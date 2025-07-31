"use client"

import { useParams } from "next/navigation"
import type React from "react"
import { useState } from "react"
import { CaseChat } from "@/components/cases/case-chat"
import {
  DEFAULT_MAX,
  DEFAULT_MIN,
  DragDivider,
} from "@/components/drag-divider"
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

  // Default starting width roughly Tailwind's w-96 (384px)
  const [chatWidth, setChatWidth] = useState<number>(DEFAULT_MIN)

  if (!caseId) {
    return <>{children}</>
  }

  return (
    <SidebarProvider>
      <AppSidebar />
      {/* Case content inset */}
      <SidebarInset className="flex-1 min-w-0 mr-px">
        <div className="flex h-full flex-col">
          <ControlsHeader />
          <div className="flex-1 overflow-y-auto">{children}</div>
        </div>
      </SidebarInset>

      {/* Drag divider */}
      <DragDivider
        className="w-1.5 shrink-0"
        value={chatWidth}
        onChange={setChatWidth}
      />

      {/* Chat inset */}
      <SidebarInset
        className="flex-none ml-px"
        style={{
          width: chatWidth,
          minWidth: DEFAULT_MIN,
          maxWidth: DEFAULT_MAX,
        }}
      >
        <CaseChat caseId={caseId} isChatOpen={true} />
      </SidebarInset>
    </SidebarProvider>
  )
}
