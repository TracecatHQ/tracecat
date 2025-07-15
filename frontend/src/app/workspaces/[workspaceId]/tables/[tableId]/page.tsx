"use client"

import { useParams } from "next/navigation"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { DatabaseTable } from "@/components/tables/table-view"
import { useGetTable } from "@/lib/hooks"
import { useWorkspace } from "@/providers/workspace"

export default function TablePage() {
  const params = useParams<{ tableId: string }>()
  const tableId = params?.tableId
  const { workspaceId } = useWorkspace()
  const { table, tableIsLoading, tableError } = useGetTable({
    tableId: tableId ?? "",
    workspaceId,
  })

  if (tableIsLoading) return <CenteredSpinner />
  if (tableError || !table)
    return (
      <AlertNotification
        message={tableError?.message ?? "Error loading table"}
        variant="error"
      />
    )

  return (
    <div className="size-full overflow-auto">
      <div className="container h-full gap-8 py-8">
        <DatabaseTable table={table} />
      </div>
    </div>
  )
}
