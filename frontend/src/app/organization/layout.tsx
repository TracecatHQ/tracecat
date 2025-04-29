"use client"

import { Suspense, useEffect } from "react"
import { useRouter } from "next/navigation"
import { useAuth } from "@/providers/auth"

import { CenteredSpinner } from "@/components/loading/spinner"
import { DynamicNavbar } from "@/components/nav/dynamic-nav"
import { OrganizationSidebarNav } from "@/components/organization/sidebar-nav"

export default function OrganizationLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const { user } = useAuth()
  const router = useRouter()
  useEffect(() => {
    if (!user?.isPrivileged()) {
      router.push("/")
    }
  }, [user, router])
  return (
    <div className="no-scrollbar flex h-screen max-h-screen flex-col overflow-hidden">
      {/* DynamicNavbar needs a WorkflowProvider and a WorkspaceProvider */}
      <DynamicNavbar />
      <div className="container h-full space-y-6 overflow-auto md:block">
        <div className="flex h-full flex-col space-y-8 lg:flex-row lg:space-y-0">
          <aside className="-mx-4 h-full lg:w-1/5">
            <OrganizationSidebarNav />
          </aside>
          <div className="no-scrollbar size-full flex-1 overflow-auto">
            <div className="container my-16">
              <Suspense fallback={<CenteredSpinner />}>{children}</Suspense>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
