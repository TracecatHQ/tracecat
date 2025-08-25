"use client"

import { RecordsWorkspaceTable } from "@/components/entities/records-workspace-table"
import { useLocalStorage } from "@/lib/hooks"

export default function EntitiesRecordsPage() {
  const [includeArchived] = useLocalStorage("entities-include-archived", false)
  return (
    <div className="size-full overflow-auto">
      <div className="container max-w-[1200px] my-8">
        <RecordsWorkspaceTable includeDeleted={includeArchived} />
      </div>
    </div>
  )
}
