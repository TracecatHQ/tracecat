"use client"

import { TablesDashboard } from "@/components/tables/tables-dashboard"

export default function TablesPage() {
  return (
    <div className="flex size-full flex-col space-y-12">
      <div className="flex w-full items-center justify-between">
        <div className="items-start space-y-3 text-left">
          <h2 className="text-2xl font-semibold tracking-tight">
            Tables
          </h2>
          <p className="text-md text-muted-foreground">
            View your workspace&apos;s tables here.
          </p>
        </div>
      </div>
      <TablesDashboard />
    </div>
  )
}
