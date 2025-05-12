import { Suspense } from "react"
import { Metadata } from "next"

import { CenteredSpinner } from "@/components/loading/spinner"
import { SidebarNav } from "@/app/workspaces/[workspaceId]/settings/sidebar-nav"

export const metadata: Metadata = {
  title: "Settings | Workspace",
}

export default async function WorkspaceSettingsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="no-scrollbar h-screen max-h-screen overflow-auto">
      <div className="container h-full space-y-6 overflow-auto md:block">
        <div className="flex h-full flex-col space-y-8 lg:flex-row lg:space-x-12 lg:space-y-0">
          <aside className="-mx-4 h-full lg:w-1/5">
            <SidebarNav />
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
