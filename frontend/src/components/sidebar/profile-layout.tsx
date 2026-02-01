"use client"

import { ProfileSidebar } from "@/components/sidebar/profile-sidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"

export function ProfileLayout({ children }: { children: React.ReactNode }) {
  return (
    <SidebarProvider>
      <ProfileSidebar />
      <SidebarInset>
        <div className="flex h-full flex-1 flex-col">
          <div className="flex-1 overflow-auto">{children}</div>
        </div>
      </SidebarInset>
    </SidebarProvider>
  )
}
