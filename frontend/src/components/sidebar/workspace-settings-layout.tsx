"use client"

import { WorkspaceSettingsSidebar } from "@/components/sidebar/workspace-settings-sidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"

export function WorkspaceSettingsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <SidebarProvider>
      <WorkspaceSettingsSidebar />
      <SidebarInset>
        <div className="flex h-full flex-1 flex-col">
          <div className="flex-1 overflow-auto">{children}</div>
        </div>
      </SidebarInset>
    </SidebarProvider>
  )
}
