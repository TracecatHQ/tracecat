"use client"

import { Suspense } from "react"
import { AuthGuard } from "@/components/auth/auth-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { SpmSidebar } from "@/components/sidebar/spm-sidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { ScopeProvider } from "@/providers/scopes"

export default function SpmLayout({ children }: { children: React.ReactNode }) {
  return (
    <ScopeProvider>
      <AuthGuard requireAuth requireOrgAdmin>
        <SidebarProvider>
          <SpmSidebar />
          <SidebarInset className="min-w-0 flex-1 mr-px">
            <div className="flex h-full flex-1 flex-col">
              <div className="min-h-0 flex-1">
                <Suspense fallback={<CenteredSpinner />}>{children}</Suspense>
              </div>
            </div>
          </SidebarInset>
        </SidebarProvider>
      </AuthGuard>
    </ScopeProvider>
  )
}
