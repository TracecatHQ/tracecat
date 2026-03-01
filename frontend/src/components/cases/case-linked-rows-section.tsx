"use client"

import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Trash2 } from "lucide-react"
import { useMemo } from "react"
import type { CaseRead, CaseTableRowRead } from "@/client"
import { Button } from "@/components/ui/button"
import { ScrollArea } from "@/components/ui/scroll-area"
import { client as apiClient } from "@/lib/api"
import { useWorkspaceId } from "@/providers/workspace-id"

export function CaseLinkedRowsSection({ caseData }: { caseData: CaseRead }) {
  const workspaceId = useWorkspaceId()
  const queryClient = useQueryClient()
  const rows = (
    (caseData as CaseRead & { rows?: CaseTableRowRead[] }).rows ?? []
  ).filter((row) => row.row_data)

  const grouped = useMemo(() => {
    const map = new Map<string, CaseTableRowRead[]>()
    for (const row of rows) {
      const key = `${row.table_id}:${row.table_name ?? "Table"}`
      const current = map.get(key) ?? []
      current.push(row)
      map.set(key, current)
    }
    return Array.from(map.entries())
  }, [rows])

  const unlinkMutation = useMutation({
    mutationFn: async (row: CaseTableRowRead) => {
      await apiClient.delete(
        `/cases/${caseData.id}/rows/${row.table_id}/${row.row_id}`,
        {
          params: {
            workspace_id: workspaceId,
          },
        }
      )
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["case", caseData.id] })
    },
  })

  if (grouped.length === 0) {
    return (
      <p className="p-2 text-sm text-muted-foreground">No linked table rows</p>
    )
  }

  return (
    <div className="space-y-4">
      {grouped.map(([key, tableRows]) => {
        const [, tableName] = key.split(":")
        const columns = Array.from(
          new Set(
            tableRows.flatMap((row) =>
              Object.keys((row.row_data as Record<string, unknown>) ?? {})
            )
          )
        )

        return (
          <div key={key} className="space-y-2">
            <p className="text-sm font-medium">{tableName}</p>
            <div className="w-full max-w-[760px] overflow-hidden rounded-md border">
              <ScrollArea className="w-full">
                <div className="max-h-72 overflow-auto">
                  <table className="w-[900px] table-fixed text-sm">
                    <thead>
                      <tr className="border-b bg-muted/50 text-left">
                        {columns.map((column) => (
                          <th key={column} className="px-3 py-2 font-medium">
                            {column}
                          </th>
                        ))}
                        <th className="w-16 px-3 py-2" />
                      </tr>
                    </thead>
                    <tbody>
                      {tableRows.map((row) => (
                        <tr key={row.id} className="border-b align-top">
                          {columns.map((column) => (
                            <td
                              key={`${row.id}-${column}`}
                              className="truncate px-3 py-2"
                            >
                              {String(
                                (row.row_data as Record<string, unknown>)?.[
                                  column
                                ] ?? ""
                              )}
                            </td>
                          ))}
                          <td className="px-2 py-1">
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => unlinkMutation.mutate(row)}
                            >
                              <Trash2 className="size-4" />
                            </Button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </ScrollArea>
            </div>
          </div>
        )
      })}
    </div>
  )
}
