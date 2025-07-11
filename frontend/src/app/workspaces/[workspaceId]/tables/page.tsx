"use client"

import { TablesDashboard } from "@/components/tables/tables-dashboard"

export default function TablesPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-8 py-8">
        <div className="space-y-4">
          <TablesDashboard />
        </div>
      </div>
    </div>
  )
}
