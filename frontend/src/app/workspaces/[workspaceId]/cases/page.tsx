import type { Metadata } from "next"
import CaseTable from "@/components/cases/case-table"

export const metadata: Metadata = {
  title: "Cases",
}

export default function CasesPage() {
  return (
    <div className="flex size-full flex-col space-y-4">
      <div className="flex w-full items-center justify-between">
        <p className="text-md text-muted-foreground">
          View your workspace&apos;s cases here.
        </p>
      </div>
      <CaseTable />
    </div>
  )
}
