"use client"

import { useState } from "react"
import { WorkspaceMember, WorkspaceResponse } from "@/client"
import { useAuth } from "@/providers/auth"
import { useWorkspace } from "@/providers/workspace"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/table"

export function WorkspaceMembersTable({
  workspace,
}: {
  workspace: WorkspaceResponse
}) {
  const [selectedUser, setSelectedUser] = useState<WorkspaceMember | null>(null)
  const { removeWorkspaceMember } = useWorkspace()
  const { user } = useAuth()

  const userIsAdmin = user?.is_superuser || user?.role === "admin"
  return (
    <AlertDialog
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedUser(null)
        }
      }}
    >
      <DataTable
        data={workspace?.members}
        columns={[
          {
            accessorKey: "email",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Email"
              />
            ),
            cell: ({ row }) => (
              <div className="text-xs">
                {row.getValue<WorkspaceMember["email"]>("email")}
              </div>
            ),
            enableSorting: true,
            enableHiding: false,
          },
          {
            accessorKey: "first_name",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="First Name"
              />
            ),
            cell: ({ row }) => (
              <div className="text-xs">
                {row.getValue<WorkspaceMember["first_name"]>("first_name") ||
                  "-"}
              </div>
            ),
            enableSorting: true,
            enableHiding: false,
          },
          {
            accessorKey: "last_name",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Last Name"
              />
            ),
            cell: ({ row }) => (
              <div className="text-xs">
                {row.getValue<WorkspaceMember["last_name"]>("last_name") || "-"}
              </div>
            ),
            enableSorting: true,
            enableHiding: false,
          },
          {
            accessorKey: "role",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Role"
              />
            ),
            cell: ({ row }) => (
              <div className="text-xs capitalize">
                {row.getValue<WorkspaceMember["role"]>("role")}
              </div>
            ),
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
                  <DropdownMenuContent align="end">
                    <DropdownMenuItem
                      onClick={() =>
                        navigator.clipboard.writeText(row.original.user_id)
                      }
                    >
                      Copy user ID
                    </DropdownMenuItem>

                    {userIsAdmin && (
                      <>
                        <DropdownMenuSeparator />
                        <DropdownMenuLabel>Admin</DropdownMenuLabel>
                        <DropdownMenuItem
                          disabled
                          onClick={() =>
                            console.log("Change role is not available yet")
                          }
                        >
                          {/* Feature not available yet */}
                          Change role
                        </DropdownMenuItem>
                        <AlertDialogTrigger asChild>
                          <DropdownMenuItem
                            className="text-rose-500 focus:text-rose-600"
                            onClick={() => {
                              setSelectedUser(row.original)
                              console.debug("Selected user", row.original)
                            }}
                          >
                            Remove from workspace
                          </DropdownMenuItem>
                        </AlertDialogTrigger>
                      </>
                    )}
                  </DropdownMenuContent>
                </DropdownMenu>
              )
            },
          },
        ]}
        toolbarProps={defaultToolbarProps}
      />
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Are you sure?</AlertDialogTitle>
          <AlertDialogDescription>
            Are you sure you want to remove this user from the workspace? This
            action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={async () => {
              if (selectedUser) {
                console.log("Removing member", selectedUser)
                await removeWorkspaceMember(selectedUser.user_id)
              }
              setSelectedUser(null)
            }}
          >
            Confirm
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
const defaultToolbarProps: DataTableToolbarProps = {
  filterProps: {
    placeholder: "Filter users by email...",
    column: "email",
  },
}
