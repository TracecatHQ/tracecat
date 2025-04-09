import CasePanelProvider from "@/providers/case-panel"

import CaseTable from "@/components/cases/case-table"

export default function CasesPage() {
  return (
    <div className="flex size-full flex-col space-y-12">
      <div className="flex w-full items-center justify-between">
        <div className="items-start space-y-3 text-left">
          <h2 className="text-2xl font-semibold tracking-tight">Cases</h2>
          <p className="text-md text-muted-foreground">
            View your workspace&apos;s cases here.
          </p>
        </div>
      </div>
      <CasePanelProvider className="h-full overflow-auto sm:w-3/5 sm:max-w-none md:w-3/5 lg:w-4/5 lg:max-w-[1200px]">
        <CaseTable />
      </CasePanelProvider>
    </div>
  )
}
