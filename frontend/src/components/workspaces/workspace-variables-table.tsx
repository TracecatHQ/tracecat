"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useState } from "react"
import { stringify } from "yaml"
import type { VariableReadMinimal } from "@/client"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  DeleteVariableAlertDialog,
  DeleteVariableAlertDialogTrigger,
} from "@/components/workspaces/delete-workspace-variable"
import {
  EditVariableDialog,
  EditVariableDialogTrigger,
} from "@/components/workspaces/edit-workspace-variable"
import { useWorkspaceVariables } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

// Custom minimal YAML viewer for table cells that supports
// syntax highlighting for keys.
function HighlightedYaml({ data }: { data: unknown }) {
  let yamlStr = ""
  try {
    yamlStr = stringify(data).trimEnd()
  } catch {
    yamlStr = String(data)
  }

  if (!yamlStr || yamlStr === "{}") {
    return <span className="text-muted-foreground">-</span>
  }

  // Simple syntax highlighting for YAML keys
  // Matches "key:" or "  key:" at start of lines
  const regex = /^(\s*[\w\-\"\']+)(:\s+)/gm
  const parts: React.ReactNode[] = []
  let lastIndex = 0
  let match

  while ((match = regex.exec(yamlStr)) !== null) {
    if (match.index > lastIndex) {
      parts.push(yamlStr.slice(lastIndex, match.index))
    }
    parts.push(
      <span key={match.index} className="text-sky-600 dark:text-sky-300">
        {match[1]}
      </span>
    )
    parts.push(match[2])
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < yamlStr.length) {
    parts.push(yamlStr.slice(lastIndex))
  }

  return (
    <div className="rounded border bg-muted/50 px-2 py-1 font-mono text-xs whitespace-pre-wrap">
      {parts}
    </div>
  )
}

export function WorkspaceVariablesTable() {
  const workspaceId = useWorkspaceId()
  const { variables, variablesIsLoading, variablesError } =
    useWorkspaceVariables(workspaceId)
  const [selectedVariable, setSelectedVariable] =
    useState<VariableReadMinimal | null>(null)
  if (variablesIsLoading) {
    return <CenteredSpinner />
  }
  if (variablesError || !variables) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading variables: ${variablesError?.message || "Variables undefined"}`}
      />
    )
  }

  return (
    <DeleteVariableAlertDialog
      selectedVariable={selectedVariable}
      setSelectedVariable={setSelectedVariable}
    >
      <EditVariableDialog
        selectedVariable={selectedVariable}
        setSelectedVariable={setSelectedVariable}
      >
        <DataTable
          data={variables}
          columns={[
            {
              accessorKey: "name",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Variable Name"
                />
              ),
              cell: ({ row }) => (
                <div className="font-mono text-xs">
                  {row.getValue<VariableReadMinimal["name"]>("name")}
                </div>
              ),
              enableSorting: true,
              enableHiding: false,
            },
            {
              accessorKey: "description",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Description"
                />
              ),
              cell: ({ row }) => (
                <div className="text-xs">
                  {row.getValue<VariableReadMinimal["description"]>(
                    "description"
                  ) || "-"}
                </div>
              ),
              enableSorting: true,
              enableHiding: false,
            },
            {
              accessorKey: "environment",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Environment"
                />
              ),
              cell: ({ row }) => (
                <div className="text-xs">
                  {row.getValue<VariableReadMinimal["environment"]>(
                    "environment"
                  ) || "-"}
                </div>
              ),
              enableSorting: true,
              enableHiding: false,
            },
            {
              accessorKey: "values",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Values"
                />
              ),
              cell: ({ row }) => {
                const values =
                  row.getValue<VariableReadMinimal["values"]>("values")
                return <HighlightedYaml data={values} />
              },
              enableSorting: false,
              enableHiding: false,
            },
            {
              id: "actions",
              enableHiding: false,
              cell: ({ row }) => {
                return (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" className="size-8 p-0">
                        <span className="sr-only">Open menu</span>
                        <DotsHorizontalIcon className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent>
                      <DropdownMenuItem
                        onClick={() =>
                          navigator.clipboard.writeText(row.original.id)
                        }
                      >
                        Copy Variable ID
                      </DropdownMenuItem>

                      <EditVariableDialogTrigger asChild>
                        <DropdownMenuItem
                          onClick={() => {
                            if (!row.original) {
                              console.error("No variable to edit")
                              return
                            }
                            setSelectedVariable(row.original)
                            console.debug(
                              "Selected variable to edit",
                              row.original
                            )
                          }}
                        >
                          Edit
                        </DropdownMenuItem>
                      </EditVariableDialogTrigger>

                      <DeleteVariableAlertDialogTrigger asChild>
                        <DropdownMenuItem
                          className="text-rose-500 focus:text-rose-600"
                          onClick={() => {
                            setSelectedVariable(row.original)
                            console.debug(
                              "Selected variable to delete",
                              row.original
                            )
                          }}
                        >
                          Delete
                        </DropdownMenuItem>
                      </DeleteVariableAlertDialogTrigger>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )
              },
            },
          ]}
          toolbarProps={defaultToolbarProps}
        />
      </EditVariableDialog>
    </DeleteVariableAlertDialog>
  )
}
const defaultToolbarProps: DataTableToolbarProps<VariableReadMinimal> = {
  filterProps: {
    placeholder: "Filter variables by name...",
    column: "name",
  },
}
