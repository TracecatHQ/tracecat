"use client"

import { useParams } from "next/navigation"
import type React from "react"
import { useEffect, useState } from "react"
import { CaseChat } from "@/components/cases/case-chat"
import { DragDivider } from "@/components/drag-divider"
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

  // Chat panel width in pixels. Default corresponds to Tailwind w-96 (384px).
  const MIN_CHAT_WIDTH = 300
  const [maxChatWidth, setMaxChatWidth] = useState<number>(1200)
  const [chatWidth, setChatWidth] = useState<number>(
    Math.min(1000, maxChatWidth)
  )

  // Update max width on mount and window resize
  useEffect(() => {
    const computeMax = () => {
      if (typeof window !== "undefined") {
        setMaxChatWidth(Math.floor(window.innerWidth * 0.6))
      }
    }
    computeMax()
    window.addEventListener("resize", computeMax)
    return () => window.removeEventListener("resize", computeMax)
  }, [])

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
        min={MIN_CHAT_WIDTH}
        max={maxChatWidth}
        onChange={setChatWidth}
      />

      {/* Chat inset */}
      <SidebarInset
        className="flex-none min-w-[300px] ml-px"
        style={{ width: chatWidth, maxWidth: maxChatWidth }}
      >
        <CaseChat caseId={caseId} isChatOpen={isChatOpen} />
      </SidebarInset>
    </SidebarProvider>
  )
}
