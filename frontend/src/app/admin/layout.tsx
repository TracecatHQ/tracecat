"use client"

import { Suspense } from "react"
import { AuthGuard } from "@/components/auth/auth-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AdminSidebar } from "@/components/sidebar/admin-sidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"

export default function AdminLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <AuthGuard requireAuth requireSuperuser redirectTo="/workspaces">
      <SidebarProvider>
        <AdminSidebar />
        <SidebarInset className="border-l-2 border-amber-500/30">
          <div className="flex h-full flex-1 flex-col">
            <div className="flex-1 overflow-auto">
              <div className="container py-16">
                <Suspense fallback={<CenteredSpinner />}>{children}</Suspense>
              </div>
            </div>
          </div>
        </SidebarInset>
      </SidebarProvider>
    </AuthGuard>
  )
}
