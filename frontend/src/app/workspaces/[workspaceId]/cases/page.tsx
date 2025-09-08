"use client"

import { useEffect } from "react"
import CaseTable from "@/components/cases/case-table"
import { CasesViewMode } from "@/components/cases/cases-view-toggle"
import { CustomFieldsView } from "@/components/cases/custom-fields-view"
import { useLocalStorage } from "@/hooks/use-local-storage"

export default function CasesPage() {
  const [view] = useLocalStorage("cases-view", CasesViewMode.Cases)

  // Update document title based on view
  useEffect(() => {
    if (typeof window !== "undefined") {
      document.title =
        view === CasesViewMode.CustomFields ? "Custom fields" : "Cases"
    }
  }, [view])

  return (
    <>
      {view === CasesViewMode.Cases ? (
        <div className="p-6 space-y-6">
          <CaseTable />
        </div>
      ) : (
        <CustomFieldsView />
      )}
    </>
  )
}
