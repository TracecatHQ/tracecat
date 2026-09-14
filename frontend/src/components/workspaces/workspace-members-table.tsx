"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useState } from "react"
import type { WorkspaceMember, WorkspaceRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
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
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"
import {
  useWorkspaceMembers,
  useWorkspaceMutations,
} from "@/hooks/use-workspace"

export function WorkspaceMembersTable({
  workspace,
}: {
  workspace: WorkspaceRead
}) {
  const canRemoveMembers = useScopeCheck("workspace:member:remove")
  const [selectedUser, setSelectedUser] = useState<WorkspaceMember | null>(null)
  const { removeMember } = useWorkspaceMutations()
  const { members, membersLoading, membersError } = useWorkspaceMembers(
    workspace.id
  )

  return (
    <AlertDialog
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedUser(null)
        }
      }}
    >
      <DataTable
        data={members}
        isLoading={membersLoading}
        error={membersError}
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
                title="First name"
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
                title="Last name"
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
            accessorKey: "role_name",
            header: ({ column }) => (
              <DataTableColumnHeader
                className="text-xs"
                column={column}
                title="Role"
              />
            ),
            cell: ({ row }) => (
              <div className="flex items-center gap-2 text-xs">
                <span className="capitalize">
                  {row.getValue<string>("role_name")}
                </span>
                {row.original.via_group && (
                  <span className="text-muted-foreground">via group</span>
                )}
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
                  <DropdownMenuContent>
                    <DropdownMenuItem
                      onClick={() =>
                        navigator.clipboard.writeText(row.original.user_id)
                      }
                    >
                      Copy user ID
                    </DropdownMenuItem>

                    {canRemoveMembers && (
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
          <AlertDialogTitle>Remove user</AlertDialogTitle>
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
                try {
                  await removeMember(selectedUser.user_id)
                } catch (error) {
                  const description =
                    error instanceof Error
                      ? error.message
                      : "The request could not be completed."
                  console.error("Failed to remove member", error)
                  toast({
                    title: "Failed to remove member",
                    description,
                    variant: "destructive",
                  })
                }
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

const defaultToolbarProps: DataTableToolbarProps<WorkspaceMember> = {
  filterProps: {
    placeholder: "Filter users by email...",
    column: "email",
  },
}
