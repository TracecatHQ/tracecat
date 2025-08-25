"use client"

import { RelationsWorkspaceTable } from "@/components/entities/relations-workspace-table"

export default function EntitiesRelationsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container max-w-[1200px] my-8">
        <RelationsWorkspaceTable />
      </div>
    </div>
  )
}
