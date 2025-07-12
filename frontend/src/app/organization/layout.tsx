"use client"

import { Suspense } from "react"
import { AuthGuard } from "@/components/auth/auth-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { OrganizationSidebar } from "@/components/sidebar/organization-sidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"

export default function OrganizationLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <AuthGuard requireAuth requirePrivileged>
      <SidebarProvider>
        <OrganizationSidebar />
        <SidebarInset>
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
