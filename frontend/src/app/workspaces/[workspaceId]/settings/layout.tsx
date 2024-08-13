import { Suspense } from "react"
import { Metadata } from "next"

import { CenteredSpinner } from "@/components/loading/spinner"
import { SidebarNav } from "@/app/workspaces/[workspaceId]/settings/sidebar-nav"

export const metadata: Metadata = {
  title: "Workspace Settings",
  description: "Workspace Settings",
}

export default async function WorkspaceSettingsLayout({
  children,
}: {
  children: React.ReactNode
}) {
  return (
    <div className="no-scrollbar h-screen max-h-screen overflow-auto">
      <div className="container h-full space-y-6 p-16 md:block">
        <div className="flex h-full flex-col space-y-8 lg:flex-row lg:space-x-12 lg:space-y-0">
          <aside className="-mx-4 h-full lg:w-1/5">
            <SidebarNav />
          </aside>
          <div className="size-full flex-1">
            <Suspense fallback={<CenteredSpinner />}>{children}</Suspense>
          </div>
        </div>
      </div>
    </div>
  )
}
