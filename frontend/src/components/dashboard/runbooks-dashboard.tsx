"use client"

import type { ColumnDef, Row } from "@tanstack/react-table"
import { format, formatDistanceToNow } from "date-fns"
import { ListTodoIcon } from "lucide-react"
import { useRouter } from "next/navigation"
import { useCallback, useMemo, useState } from "react"
import type { PromptRead } from "@/client"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Badge } from "@/components/ui/badge"
import { Checkbox } from "@/components/ui/checkbox"
import { toast } from "@/components/ui/use-toast"
import { useDeletePrompt, useListPrompts } from "@/hooks/use-prompt"
import { useWorkspace } from "@/providers/workspace"

export function RunbooksDashboard() {
  const [isDeleting, setIsDeleting] = useState(false)
  const { workspaceId } = useWorkspace()
  const router = useRouter()

  const {
    data: prompts,
    isLoading,
    error,
  } = useListPrompts({
    workspaceId,
    limit: 100,
  })

  const { deletePrompt } = useDeletePrompt(workspaceId)

  const handleOnClickRow = (row: Row<PromptRead>) => () => {
    const promptId = row.original.id
    router.push(`/workspaces/${workspaceId}/runbooks/${promptId}`)
  }

  const handleDeleteRows = useCallback(
    async (selectedRows: Row<PromptRead>[]) => {
      if (selectedRows.length === 0) return

      try {
        setIsDeleting(true)
        // Get IDs of selected cases
        const promptIds = selectedRows.map((row) => row.original.id)

        // Call the delete operation
        await Promise.all(promptIds.map((promptId) => deletePrompt(promptId)))

        // Show success toast
        toast({
          title: `${promptIds.length} runbook(s) deleted`,
          description: "The selected runbooks have been deleted successfully.",
        })

        // Refresh the cases list
      } catch (error) {
        console.error("Failed to delete cases:", error)
      } finally {
        setIsDeleting(false)
      }
    },
    [deletePrompt, toast]
  )

  const toolbarProps = useMemo(() => {
    return {
      filterProps: {
        placeholder: "Filter runbooks by title...",
        column: "title",
      },
    } as DataTableToolbarProps<PromptRead>
  }, [])

  const columns: ColumnDef<PromptRead>[] = useMemo(
    () => [
      {
        id: "select",
        header: ({ table }) => (
          <Checkbox
            className="border-foreground/50"
            checked={
              table.getIsAllPageRowsSelected() ||
              (table.getIsSomePageRowsSelected() && "indeterminate")
            }
            onCheckedChange={(value) =>
              table.toggleAllPageRowsSelected(!!value)
            }
            aria-label="Select all"
          />
        ),
        cell: ({ row }) => (
          <div onClick={(e) => e.stopPropagation()}>
            <Checkbox
              className="border-foreground/50"
              checked={row.getIsSelected()}
              onCheckedChange={(value) => row.toggleSelected(!!value)}
              aria-label="Select row"
            />
          </div>
        ),
        enableSorting: false,
        enableHiding: false,
      },
      {
        accessorKey: "title",
        header: ({ column }) => (
          <DataTableColumnHeader column={column} title="Title" />
        ),
        cell: ({ row }) => {
          const title = row.getValue<string>("title")
          return (
            <div className="flex items-center space-x-2">
              <ListTodoIcon className="h-4 w-4 text-muted-foreground" />
              <span className="max-w-[300px] truncate font-medium">
                {title}
              </span>
            </div>
          )
        },
        enableSorting: true,
      },
      {
        accessorKey: "content",
        header: ({ column }) => (
          <DataTableColumnHeader column={column} title="Content" />
        ),
        cell: ({ row }) => {
          const content = row.getValue<string>("content")
          return (
            <div className="max-w-[400px] truncate text-sm text-muted-foreground">
              {content}
            </div>
          )
        },
        enableSorting: false,
      },
      {
        accessorKey: "tools",
        header: ({ column }) => (
          <DataTableColumnHeader column={column} title="Tools" />
        ),
        cell: ({ row }) => {
          const tools = row.getValue<string[]>("tools")
          return (
            <div className="flex items-center space-x-1">
              <Badge variant="secondary" className="text-xs">
                {tools.length} tool{tools.length !== 1 ? "s" : ""}
              </Badge>
            </div>
          )
        },
        enableSorting: true,
      },
      {
        accessorKey: "created_at",
        header: ({ column }) => (
          <DataTableColumnHeader column={column} title="Created" />
        ),
        cell: ({ row }) => {
          const createdAt = row.getValue<string>("created_at")
          const date = new Date(createdAt)
          return (
            <div className="flex items-center gap-1 text-xs text-muted-foreground">
              <span>{format(date, "MMM d, yyyy")}</span>
              <span>({formatDistanceToNow(date, { addSuffix: true })})</span>
            </div>
          )
        },
        enableSorting: true,
      },
    ],
    []
  )

  if (isLoading) {
    return <CenteredSpinner />
  }

  if (error) {
    return (
      <div className="container mx-auto p-6">
        <div className="text-center text-red-600">
          Error loading runbooks: {error.message}
        </div>
      </div>
    )
  }

  return (
    <div className="container mx-auto space-y-6 p-6">
      <DataTable
        columns={columns}
        data={prompts || []}
        onClickRow={handleOnClickRow}
        onDeleteRows={handleDeleteRows}
        isLoading={isLoading || isDeleting}
        toolbarProps={toolbarProps}
      />
    </div>
  )
}
