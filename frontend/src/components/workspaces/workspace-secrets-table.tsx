"use client"

import React, { useState } from "react"
import { SecretReadMinimal } from "@/client"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"

import { useWorkspaceSecrets } from "@/lib/hooks"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import {
  DeleteSecretAlertDialog,
  DeleteSecretAlertDialogTrigger,
} from "@/components/workspaces/delete-workspace-secret"
import {
  EditCredentialsDialog,
  EditCredentialsDialogTrigger,
} from "@/components/workspaces/edit-workspace-secret"

export function WorkspaceSecretsTable() {
  const { secrets, secretsIsLoading, secretsError } = useWorkspaceSecrets()
  const [selectedSecret, setSelectedSecret] =
    useState<SecretReadMinimal | null>(null)
  if (secretsIsLoading) {
    return <CenteredSpinner />
  }
  if (secretsError || !secrets) {
    return (
      <AlertNotification
        level="error"
        message={`Error loading secrets: ${secretsError?.message || "Secrets undefined"}`}
      />
    )
  }

  return (
    <DeleteSecretAlertDialog
      selectedSecret={selectedSecret}
      setSelectedSecret={setSelectedSecret}
    >
      <EditCredentialsDialog
        selectedSecret={selectedSecret}
        setSelectedSecret={setSelectedSecret}
      >
        <DataTable
          data={secrets}
          columns={[
            {
              accessorKey: "name",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Secret Name"
                />
              ),
              cell: ({ row }) => (
                <div className="font-mono text-xs">
                  {row.getValue<SecretReadMinimal["name"]>("name")}
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
                  {row.getValue<SecretReadMinimal["description"]>(
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
                  {row.getValue<SecretReadMinimal["environment"]>(
                    "environment"
                  ) || "-"}
                </div>
              ),
              enableSorting: true,
              enableHiding: false,
            },
            {
              accessorKey: "keys",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Secret Keys"
                />
              ),
              cell: ({ row }) => {
                const keys = row.getValue<SecretReadMinimal["keys"]>("keys")
                return (
                  <div className="flex-auto space-x-4 text-xs">
                    {keys.map((key, idx) => (
                      <Badge
                        variant="secondary"
                        className="font-mono text-xs"
                        key={idx}
                      >
                        {key}
                      </Badge>
                    ))}
                  </div>
                )
              },
              enableSorting: true,
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
                        Copy Secret ID
                      </DropdownMenuItem>

                      <EditCredentialsDialogTrigger asChild>
                        <DropdownMenuItem
                          onClick={() => {
                            if (!row.original) {
                              console.error("No secret to edit")
                              return
                            }
                            setSelectedSecret(row.original)
                            console.debug(
                              "Selected secret to edit",
                              row.original
                            )
                          }}
                        >
                          Edit
                        </DropdownMenuItem>
                      </EditCredentialsDialogTrigger>

                      <DeleteSecretAlertDialogTrigger asChild>
                        <DropdownMenuItem
                          className="text-rose-500 focus:text-rose-600"
                          onClick={() => {
                            setSelectedSecret(row.original)
                            console.debug(
                              "Selected secret to delete",
                              row.original
                            )
                          }}
                        >
                          Delete
                        </DropdownMenuItem>
                      </DeleteSecretAlertDialogTrigger>
                    </DropdownMenuContent>
                  </DropdownMenu>
                )
              },
            },
          ]}
          toolbarProps={defaultToolbarProps}
        />
      </EditCredentialsDialog>
    </DeleteSecretAlertDialog>
  )
}
const defaultToolbarProps: DataTableToolbarProps = {
  filterProps: {
    placeholder: "Filter secrets by name...",
    column: "name",
  },
}
