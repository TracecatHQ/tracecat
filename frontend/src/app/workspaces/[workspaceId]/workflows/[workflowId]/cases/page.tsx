import CasesProvider from "@/providers/cases"

import CaseTable from "@/components/cases/table"

export default function CasesPage() {
  return (
    <CasesProvider>
      <div className="flex h-screen flex-col overflow-auto">
        <div className="flex-1 space-y-8 p-16">
          <CaseTable />
        </div>
      </div>
    </CasesProvider>
  )
}
