"use client"

import { useCallback, useState } from "react"
import { WorkspaceMember, WorkspaceRead, WorkspaceRole } from "@/client"
import { useAuth } from "@/providers/auth"
import { useWorkspace } from "@/providers/workspace"
import { DotsHorizontalIcon } from "@radix-ui/react-icons"

import { userIsPrivileged } from "@/lib/auth"
import { WorkspaceRoleEnum } from "@/lib/workspace"
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"

export function WorkspaceMembersTable({
  workspace,
}: {
  workspace: WorkspaceRead
}) {
  const { user } = useAuth()
  const [selectedUser, setSelectedUser] = useState<WorkspaceMember | null>(null)
  const [isChangeRoleOpen, setIsChangeRoleOpen] = useState(false)
  const { membership, removeWorkspaceMember, updateWorkspaceMembership } =
    useWorkspace()

  const handleChangeRole = useCallback(
    async (role: WorkspaceRole) => {
      try {
        if (!selectedUser) {
          return toast({
            title: "No user selected",
            description: "Please select a user to change role",
          })
        }
        if (selectedUser.workspace_role === role) {
          return toast({
            title: "No changes made",
            description: `User ${selectedUser.email} is already a ${role}`,
          })
        }
        console.log("Changing role", selectedUser, role)
        await updateWorkspaceMembership({
          userId: selectedUser.user_id,
          workspaceId: workspace.id,
          requestBody: { role },
        })
      } catch (error) {
        console.log("Failed to change role", error)
      } finally {
        setIsChangeRoleOpen(false)
        setSelectedUser(null)
      }
    },
    [selectedUser, updateWorkspaceMembership, workspace.id]
  )
  const userIsAdmin = userIsPrivileged(user, membership)
  return (
    <Dialog open={isChangeRoleOpen} onOpenChange={setIsChangeRoleOpen}>
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
                  {row.getValue<WorkspaceMember["last_name"]>("last_name") ||
                    "-"}
                </div>
              ),
              enableSorting: true,
              enableHiding: false,
            },
            {
              accessorKey: "workspace_role",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Role"
                />
              ),
              cell: ({ row }) => (
                <div className="text-xs capitalize">
                  {row.getValue<WorkspaceMember["workspace_role"]>(
                    "workspace_role"
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

                      {userIsAdmin && (
                        <>
                          <DialogTrigger asChild>
                            <DropdownMenuItem
                              onClick={() => {
                                setSelectedUser(row.original)
                                setIsChangeRoleOpen(true)
                              }}
                            >
                              Change role
                            </DropdownMenuItem>
                          </DialogTrigger>

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
                    await removeWorkspaceMember(selectedUser.user_id)
                  } catch (error) {
                    console.log("Failed to remove member", error)
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
      <ChangeUserRoleDialog
        selectedUser={selectedUser}
        setOpen={setIsChangeRoleOpen}
        onConfirm={handleChangeRole}
      />
    </Dialog>
  )
}

function ChangeUserRoleDialog({
  selectedUser,
  setOpen,
  onConfirm,
}: {
  selectedUser: WorkspaceMember | null
  setOpen: (open: boolean) => void
  onConfirm: (role: WorkspaceRole) => void
}) {
  const [newRole, setNewRole] = useState<WorkspaceRole>(
    selectedUser?.workspace_role || "editor"
  )
  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Change User Role</DialogTitle>
        <DialogDescription>
          Select a new role for {selectedUser?.email}
        </DialogDescription>
      </DialogHeader>
      <Select
        value={newRole}
        onValueChange={(value) => setNewRole(value as WorkspaceRole)}
      >
        <SelectTrigger>
          <SelectValue placeholder="Select a role" />
        </SelectTrigger>
        <SelectContent>
          {WorkspaceRoleEnum.map((role) => (
            <SelectItem key={role} value={role}>
              <span className="capitalize">{role}</span>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <DialogFooter>
        <Button variant="outline" onClick={() => setOpen(false)}>
          Cancel
        </Button>
        <Button onClick={() => onConfirm(newRole)}>Change Role</Button>
      </DialogFooter>
    </DialogContent>
  )
}

const defaultToolbarProps: DataTableToolbarProps<WorkspaceMember> = {
  filterProps: {
    placeholder: "Filter users by email...",
    column: "email",
  },
}
