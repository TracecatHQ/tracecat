"use client"

import { TablesDashboard } from "@/components/tables/tables-dashboard"

export default function TablesPage() {
  return (
    <div className="size-full overflow-auto px-3 py-6">
      <div className="flex h-full flex-col space-y-6">
        <TablesDashboard />
      </div>
    </div>
  )
}
