import CasesProvider from "@/providers/cases"

import CaseTable from "@/components/cases/table"
import { AlertNotification } from "@/components/notifications"

export default function CasesPage() {
  return (
    <CasesProvider>
      <div className="flex h-screen flex-col overflow-auto">
        <div className="flex-1 space-y-8 p-16">
          {process.env.NEXT_PUBLIC_APP_ENV === "production" && (
            <AlertNotification
              message="Cases is in preview mode, and may not work as expected"
              className="max-w-[600px]"
            />
          )}
          <CaseTable />
        </div>
      </div>
    </CasesProvider>
  )
}
