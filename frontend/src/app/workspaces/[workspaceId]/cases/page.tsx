"use client"

import { useEffect } from "react"
import CaseTable from "@/components/cases/case-table"

export default function CasesPage() {
  useEffect(() => {
    if (typeof window !== "undefined") {
      document.title = "Cases"
    }
  }, [])

  return (
    <div className="size-full overflow-auto p-6 space-y-6">
      <CaseTable />
    </div>
  )
}
