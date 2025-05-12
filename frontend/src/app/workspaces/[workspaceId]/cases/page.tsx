import { Metadata } from "next"
import CasePanelProvider from "@/providers/case-panel"

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
      <CasePanelProvider className="h-full overflow-auto sm:w-3/5 sm:max-w-none md:w-3/5 lg:w-4/5 lg:max-w-[1200px]">
        <CaseTable />
      </CasePanelProvider>
    </div>
  )
}
