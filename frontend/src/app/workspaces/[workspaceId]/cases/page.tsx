"use client"

import { useSearchParams } from "next/navigation"
import { useEffect } from "react"
import CaseTable from "@/components/cases/case-table"
import { CaseTagsSidebar } from "@/components/cases/case-tags-sidebar"
import { CasesViewMode } from "@/components/cases/cases-view-toggle"
import { useWorkspaceId } from "@/providers/workspace-id"

export default function CasesPage() {
  const searchParams = useSearchParams()
  const workspaceId = useWorkspaceId()

  useEffect(() => {
    if (typeof window !== "undefined") {
      document.title = "Cases"
    }
  }, [])

  const viewParam = searchParams?.get("view") as CasesViewMode | null
  const view = viewParam ?? CasesViewMode.Cases

  if (view === CasesViewMode.Tags) {
    return (
      <div className="size-full overflow-auto px-3 py-6">
        <div className="flex h-full flex-row gap-4">
          <div className="w-48">
            <CaseTagsSidebar workspaceId={workspaceId} />
          </div>
          <div className="flex-1">
            <CaseTable />
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="size-full overflow-auto px-3 py-6 space-y-6">
      <CaseTable />
    </div>
  )
}
