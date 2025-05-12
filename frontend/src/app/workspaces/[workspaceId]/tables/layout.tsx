"use client"

import { Suspense, useEffect } from "react"
import { useWorkspace } from "@/providers/workspace"

import { CenteredSpinner } from "@/components/loading/spinner"
import { TablesSidebar } from "@/components/tables/tables-side-nav"

export default function TablesLayout({
  children,
}: {
  children: React.ReactNode
}) {
  const { workspaceId } = useWorkspace()

  useEffect(() => {
    document.title = `Tables`
  }, [])

  return (
    <div className="container grid h-full grid-cols-6 gap-8 py-16">
      <div className="col-span-1">
        <TablesSidebar workspaceId={workspaceId} />
      </div>
      <div className="no-scrollbar col-span-5 flex size-full flex-col overflow-auto">
        <Suspense fallback={<CenteredSpinner />}>{children}</Suspense>
      </div>
    </div>
  )
}
