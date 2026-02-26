"use client"

import { TablesDashboard } from "@/components/tables/tables-dashboard"

export default function TablesPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full flex-col space-y-12 py-8">
        <TablesDashboard />
      </div>
    </div>
  )
}
