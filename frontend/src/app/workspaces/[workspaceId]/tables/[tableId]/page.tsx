"use client"

import { ArrowLeftIcon } from "lucide-react"
import { useParams } from "next/navigation"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { TableInsertButton } from "@/components/tables/table-insert-button"
import { DatabaseTable } from "@/components/tables/table-view"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbSeparator,
} from "@/components/ui/breadcrumb"
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
    <div className="flex size-full flex-col space-y-8">
      <div className="flex w-full items-center justify-between">
        <div className="items-start space-y-3 text-left">
          <Breadcrumb>
            <BreadcrumbList>
              <BreadcrumbItem>
                <BreadcrumbLink
                  href={`/workspaces/${workspaceId}/tables`}
                  className="flex items-center"
                >
                  <ArrowLeftIcon className="mr-2 size-4" />
                  Tables
                </BreadcrumbLink>
              </BreadcrumbItem>
              <BreadcrumbSeparator>{"/"}</BreadcrumbSeparator>
              <BreadcrumbItem>
                <BreadcrumbLink>{table.name}</BreadcrumbLink>
              </BreadcrumbItem>
            </BreadcrumbList>
          </Breadcrumb>
        </div>
        <TableInsertButton />
      </div>
      <DatabaseTable table={table} />
    </div>
  )
}
