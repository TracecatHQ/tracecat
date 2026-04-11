"use client"

import { XIcon } from "lucide-react"
import type React from "react"
import { createContext, useCallback, useContext, useRef, useState } from "react"
import { ApprovalsChat } from "@/components/approvals/approvals-chat"
import { ControlsHeader } from "@/components/nav/controls-header"
import { AppSidebar } from "@/components/sidebar/app-sidebar"
import { Button } from "@/components/ui/button"
import { ResizableSidebar } from "@/components/ui/resizable-sidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import type { ApprovalSessionItem } from "@/lib/agents"

interface ApprovalsChatContextValue {
  selectedSession: ApprovalSessionItem | null
  setSelectedSession: (session: ApprovalSessionItem | null) => void
  chatOpen: boolean
  setChatOpen: (open: boolean) => void
  registerOnClose: (callback: () => void) => void
}

const ApprovalsChatContext = createContext<ApprovalsChatContextValue | null>(
  null
)

export function useApprovalsChat() {
  const context = useContext(ApprovalsChatContext)
  if (!context) {
    throw new Error("useApprovalsChat must be used within ApprovalsLayout")
  }
  return context
}

export default function ApprovalsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const [selectedSession, setSelectedSession] =
    useState<ApprovalSessionItem | null>(null)
  const [chatOpen, setChatOpen] = useState(false)
  const onCloseCallbackRef = useRef<(() => void) | null>(null)

  const registerOnClose = useCallback((callback: () => void) => {
    onCloseCallbackRef.current = callback
  }, [])

  const handleCloseChat = () => {
    setSelectedSession(null)
    setChatOpen(false)
    onCloseCallbackRef.current?.()
  }

  return (
    <ApprovalsChatContext.Provider
      value={{
        selectedSession,
        setSelectedSession,
        chatOpen,
        setChatOpen,
        registerOnClose,
      }}
    >
      <SidebarProvider>
        <AppSidebar />
        {/* Main content inset */}
        <SidebarInset className="min-w-0 flex-1 mr-px">
          <div className="flex h-full flex-col">
            <ControlsHeader
              onToggleChat={
                selectedSession ? () => setChatOpen((prev) => !prev) : undefined
              }
            />
            <div className="flex-1 overflow-y-auto">{children}</div>
          </div>
        </SidebarInset>

        {/* Chat sidebar */}
        {chatOpen && selectedSession && (
          <ResizableSidebar initial={450} min={350} max={700}>
            <div className="flex h-full flex-col">
              {/* Header */}
              <div className="flex shrink-0 items-center justify-between px-4 py-2">
                <div className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate text-sm font-medium">
                    {selectedSession.parent_workflow?.alias ||
                      selectedSession.parent_workflow?.title ||
                      selectedSession.title}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {selectedSession.statusLabel}
                  </span>
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  className="size-6 p-0"
                  onClick={handleCloseChat}
                >
                  <XIcon className="size-4" />
                </Button>
              </div>

              {/* Chat content */}
              <div className="min-h-0 flex-1">
                <ApprovalsChat session={selectedSession} />
              </div>
            </div>
          </ResizableSidebar>
        )}
      </SidebarProvider>
    </ApprovalsChatContext.Provider>
  )
}
