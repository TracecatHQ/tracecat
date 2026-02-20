"use client"

import { Suspense } from "react"
import { AuthGuard } from "@/components/auth/auth-guard"
import { CenteredSpinner } from "@/components/loading/spinner"
import { RegistrySidebar } from "@/components/sidebar/registry-sidebar"
import { SidebarInset, SidebarProvider } from "@/components/ui/sidebar"
import { ScopeProvider } from "@/providers/scopes"

export default function RegistryLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <ScopeProvider>
      <AuthGuard requireAuth>
        <SidebarProvider>
          <RegistrySidebar />
          <SidebarInset>
            <div className="flex h-full flex-1 flex-col">
              <div className="flex-1 overflow-auto">
                <div className="container my-16">
                  <Suspense fallback={<CenteredSpinner />}>{children}</Suspense>
                </div>
              </div>
            </div>
          </SidebarInset>
        </SidebarProvider>
      </AuthGuard>
    </ScopeProvider>
  )
}
