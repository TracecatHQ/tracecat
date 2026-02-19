"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useCallback, useState } from "react"
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
  useWorkspaceMembers,
  useWorkspaceMutations,
} from "@/hooks/use-workspace"
import { useRbacRoles } from "@/lib/hooks"

export function WorkspaceMembersTable({
  workspace,
}: {
  workspace: WorkspaceRead
}) {
  const canManageMembers = useScopeCheck("workspace:member:update")
  const [selectedUser, setSelectedUser] = useState<WorkspaceMember | null>(null)
  const [isChangeRoleOpen, setIsChangeRoleOpen] = useState(false)
  const { removeMember, updateMember } = useWorkspaceMutations()
  const { members, membersLoading, membersError } = useWorkspaceMembers(
    workspace.id
  )

  const handleChangeRole = useCallback(
    async (roleName: string) => {
      try {
        if (!selectedUser) {
          return toast({
            title: "No user selected",
            description: "Please select a user to change role",
          })
        }
        if (selectedUser.role_name === roleName) {
          return toast({
            title: "No changes made",
            description: `User ${selectedUser.email} is already a ${roleName}`,
          })
        }
        console.log("Changing role", selectedUser, roleName)
        await updateMember({
          userId: selectedUser.user_id,
          workspaceId: workspace.id,
          requestBody: {
            role_name: roleName,
          },
        })
      } catch (error) {
        console.log("Failed to change role", error)
      } finally {
        setIsChangeRoleOpen(false)
        setSelectedUser(null)
      }
    },
    [selectedUser, updateMember, workspace.id]
  )
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
                  {row.getValue<WorkspaceMember["last_name"]>("last_name") ||
                    "-"}
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
                <div className="text-xs capitalize">
                  {row.getValue<string>("role_name")}
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

                      {canManageMembers && (
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
                    await removeMember(selectedUser.user_id)
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
  onConfirm: (roleName: string) => void
}) {
  const [newRoleName, setNewRoleName] = useState<string>(
    selectedUser?.role_name || "Editor"
  )
  const { roles } = useRbacRoles()
  const workspaceRoles = roles.filter(
    (r) => !r.slug || r.slug.startsWith("workspace-")
  )

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Change user role</DialogTitle>
        <DialogDescription>
          Select a new role for {selectedUser?.email}
        </DialogDescription>
      </DialogHeader>
      <Select
        value={newRoleName}
        onValueChange={(value) => setNewRoleName(value)}
      >
        <SelectTrigger>
          <SelectValue placeholder="Select a role" />
        </SelectTrigger>
        <SelectContent>
          {workspaceRoles.map((role) => (
            <SelectItem key={role.id} value={role.name}>
              {role.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <DialogFooter>
        <Button variant="outline" onClick={() => setOpen(false)}>
          Cancel
        </Button>
        <Button onClick={() => onConfirm(newRoleName)}>Change role</Button>
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
