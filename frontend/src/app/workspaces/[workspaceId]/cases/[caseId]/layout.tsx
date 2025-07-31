"use client"

import { GripVertical } from "lucide-react"
import { useParams } from "next/navigation"
import type React from "react"
import { useEffect, useState } from "react"
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

  // Handler to initiate drag resizing
  const handleDividerMouseDown = (
    e: React.MouseEvent<HTMLDivElement, MouseEvent>
  ) => {
    e.preventDefault()

    const startX = e.clientX
    const startWidth = chatWidth

    const onMouseMove = (moveEvent: MouseEvent) => {
      const deltaX = startX - moveEvent.clientX
      let newWidth = startWidth + deltaX
      // Clamp within min/max bounds
      newWidth = Math.min(Math.max(newWidth, MIN_CHAT_WIDTH), maxChatWidth)
      setChatWidth(newWidth)
    }

    const onMouseUp = () => {
      window.removeEventListener("mousemove", onMouseMove)
      window.removeEventListener("mouseup", onMouseUp)
    }

    window.addEventListener("mousemove", onMouseMove)
    window.addEventListener("mouseup", onMouseUp)
  }

  if (!caseId) {
    return <>{children}</>
  }

  return (
    <SidebarProvider>
      <AppSidebar />
      {/* Case content inset */}
      <SidebarInset className="flex-1 min-w-0">
        <div className="flex h-full flex-col">
          <ControlsHeader />
          <div className="flex-1 overflow-y-auto">{children}</div>
        </div>
      </SidebarInset>

      {/* Drag divider */}
      <div
        className="group relative w-3 shrink-0 cursor-col-resize flex items-center justify-center"
        onMouseDown={handleDividerMouseDown}
      >
        {/* slim line that appears on hover */}
        <div className="absolute inset-y-3 w-px bg-transparent transition-colors duration-150 group-hover:bg-border" />
        {/* grip icon shows on hover */}
        <GripVertical className="h-4 w-4 text-border opacity-0 transition-opacity duration-150 group-hover:opacity-100" />
      </div>

      {/* Chat inset */}
      <SidebarInset
        className="flex-none min-w-[300px]"
        style={{ width: chatWidth, maxWidth: maxChatWidth }}
      >
        <CaseChat caseId={caseId} isChatOpen={isChatOpen} />
      </SidebarInset>
    </SidebarProvider>
  )
}
