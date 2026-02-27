"use client"

import {
  useParams,
  usePathname,
  useRouter,
  useSearchParams,
} from "next/navigation"
import type React from "react"
import { useEffect, useState } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { CaseSelectionProvider } from "@/components/cases/case-selection-context"
import { ChatInterface } from "@/components/chat/chat-interface"
import { ControlsHeader } from "@/components/nav/controls-header"
import { AppSidebar } from "@/components/sidebar/app-sidebar"
import { ResizableSidebar } from "@/components/ui/resizable-sidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { useWorkspaceId } from "@/providers/workspace-id"

export function CasesRouteLayout({ children }: { children: React.ReactNode }) {
  const params = useParams<{ caseId?: string }>()
  const workspaceId = useWorkspaceId()
  const canExecuteAgents = useScopeCheck("agent:execute")
  const pathname = usePathname()
  const searchParams = useSearchParams()
  const router = useRouter()
  const [chatOpen, setChatOpen] = useState(false)

  useEffect(() => {
    if (searchParams?.get("chat") !== "open") {
      return
    }
    setChatOpen(true)
    const nextParams = new URLSearchParams(searchParams.toString())
    nextParams.delete("chat")
    const query = nextParams.toString()
    router.replace(query ? `${pathname}?${query}` : pathname)
  }, [pathname, searchParams, router])

  const canShowChat = canExecuteAgents === true

  if (params?.caseId) {
    return <>{children}</>
  }

  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset className="min-w-0 flex-1 mr-px">
        <CaseSelectionProvider>
          <div className="flex h-full flex-col">
            <ControlsHeader
              isChatOpen={chatOpen}
              onToggleChat={
                canShowChat ? () => setChatOpen((prev) => !prev) : undefined
              }
            />
            <div className="flex-1 overflow-y-auto">{children}</div>
          </div>
        </CaseSelectionProvider>
      </SidebarInset>

      {canShowChat && chatOpen && (
        <ResizableSidebar initial={450} min={350} max={700}>
          <div className="flex h-full flex-col">
            <ChatInterface entityType="copilot" entityId={workspaceId} />
          </div>
        </ResizableSidebar>
      )}
    </SidebarProvider>
  )
}
