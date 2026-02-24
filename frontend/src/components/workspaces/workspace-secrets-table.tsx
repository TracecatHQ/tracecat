"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { AlertTriangleIcon } from "lucide-react"
import { useState } from "react"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  DeleteSecretAlertDialog,
  DeleteSecretAlertDialogTrigger,
} from "@/components/workspaces/delete-workspace-secret"
import {
  EditCredentialsDialog,
  EditCredentialsDialogTrigger,
} from "@/components/workspaces/edit-workspace-secret"
import { useWorkspaceSecrets, type WorkspaceSecretListItem } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

const secretTypeLabels: Record<WorkspaceSecretListItem["type"], string> = {
  custom: "Custom",
  "ssh-key": "SSH key",
  mtls: "mTLS",
  "ca-cert": "CA certificate",
  "github-app": "GitHub app",
}

export function WorkspaceSecretsTable() {
  const workspaceId = useWorkspaceId()
  const { secrets, secretsIsLoading, secretsError } =
    useWorkspaceSecrets(workspaceId)
  const [selectedSecret, setSelectedSecret] =
    useState<WorkspaceSecretListItem | null>(null)
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

  const corruptedSecrets = secrets.filter((secret) => secret.is_corrupted)

  return (
    <>
      {corruptedSecrets.length > 0 && (
        <Alert>
          <AlertTriangleIcon className="size-4 !text-amber-600" />
          <AlertTitle>Some secrets could not be decrypted</AlertTitle>
          <AlertDescription>
            Failed to decrypt key names and values for{" "}
            {corruptedSecrets.map((secret) => secret.name).join(", ")}. Secret
            names are still available, but you must re-enter all key names and
            values to recover these secrets. For SSH keys, delete and recreate
            the secret.
          </AlertDescription>
        </Alert>
      )}
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
                  <div className="flex items-center gap-2 font-mono text-xs">
                    {row.original.is_corrupted ? (
                      <span
                        aria-label="Secret is corrupted"
                        className="size-1.5 rounded-full bg-amber-500"
                        title="Unable to decrypt this secret"
                      />
                    ) : null}
                    <span>
                      {row.getValue<WorkspaceSecretListItem["name"]>("name")}
                    </span>
                  </div>
                ),
                enableSorting: true,
                enableHiding: false,
              },
              {
                accessorKey: "type",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Secret Type"
                  />
                ),
                cell: ({ row }) => {
                  const type =
                    row.getValue<WorkspaceSecretListItem["type"]>("type")
                  const label = secretTypeLabels[type] ?? type
                  return (
                    <Badge variant="secondary" className="text-xs">
                      {label}
                    </Badge>
                  )
                },
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
                    {row.getValue<WorkspaceSecretListItem["description"]>(
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
                    {row.getValue<WorkspaceSecretListItem["environment"]>(
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
                    title="Secret keys"
                  />
                ),
                cell: ({ row }) => {
                  if (row.original.is_corrupted) {
                    return (
                      <span className="text-xs">
                        Unavailable (reconfigure required)
                      </span>
                    )
                  }

                  const keys =
                    row.getValue<WorkspaceSecretListItem["keys"]>("keys")
                  if (!keys?.length) {
                    return <div className="text-xs">-</div>
                  }
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
                  const isCorruptedSshKey =
                    row.original.is_corrupted && row.original.type === "ssh-key"
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
                            disabled={isCorruptedSshKey}
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
                            {isCorruptedSshKey
                              ? "Edit (delete and recreate)"
                              : "Edit"}
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
    </>
  )
}
const defaultToolbarProps: DataTableToolbarProps<WorkspaceSecretListItem> = {
  filterProps: {
    placeholder: "Filter secrets by name...",
    column: "name",
  },
}
