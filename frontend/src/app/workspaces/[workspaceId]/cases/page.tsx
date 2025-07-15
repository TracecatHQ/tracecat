import type { Metadata } from "next"
import CaseTable from "@/components/cases/case-table"

export const metadata: Metadata = {
  title: "Cases",
}

export default function CasesPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full flex-col space-y-12 py-8">
        <CaseTable />
      </div>
    </div>
  )
}
