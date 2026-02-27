"use client"

import { usePathname, useRouter, useSearchParams } from "next/navigation"
import type React from "react"
import { useEffect, useState } from "react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { ControlsHeader } from "@/components/nav/controls-header"
import { AppSidebar } from "@/components/sidebar/app-sidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { WorkspaceCopilotSidebar } from "@/components/workspaces/workspace-copilot-sidebar"
import { useEntitlements } from "@/hooks/use-entitlements"

interface WorkspaceCollectionRouteLayoutProps {
  children: React.ReactNode
  detailId?: string
  wrapMainContent?: (content: React.ReactNode) => React.ReactNode
}

export function WorkspaceCollectionRouteLayout({
  children,
  detailId,
  wrapMainContent,
}: WorkspaceCollectionRouteLayoutProps) {
  const canExecuteAgents = useScopeCheck("agent:execute")
  const { hasEntitlement } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")
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

  const canShowChat = canExecuteAgents === true && agentAddonsEnabled

  if (detailId) {
    return <>{children}</>
  }

  const mainContent = (
    <div className="flex h-full flex-col">
      <ControlsHeader
        onToggleChat={
          canShowChat ? () => setChatOpen((prev) => !prev) : undefined
        }
      />
      <div className="flex-1 overflow-y-auto">{children}</div>
    </div>
  )

  return (
    <SidebarProvider>
      <AppSidebar />
      <SidebarInset className="min-w-0 flex-1 mr-px">
        {wrapMainContent ? wrapMainContent(mainContent) : mainContent}
      </SidebarInset>

      {canShowChat && chatOpen && <WorkspaceCopilotSidebar />}
    </SidebarProvider>
  )
}
