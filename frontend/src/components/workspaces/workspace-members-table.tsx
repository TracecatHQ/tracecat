"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useCallback, useState } from "react"
import type {
  WorkspaceMemberOrInvitation,
  WorkspaceRead,
  WorkspaceRole,
} from "@/client"
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
import { Badge } from "@/components/ui/badge"
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
  DropdownMenuSeparator,
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
import { useAuth } from "@/hooks/use-auth"
import {
  useWorkspaceMembers,
  useWorkspaceMutations,
} from "@/hooks/use-workspace"
import { useWorkspaceInvitations } from "@/lib/hooks"
import { WorkspaceRoleEnum } from "@/lib/workspace"

export function WorkspaceMembersTable({
  workspace,
}: {
  workspace: WorkspaceRead
}) {
  const { user } = useAuth()
  const [selectedItem, setSelectedItem] =
    useState<WorkspaceMemberOrInvitation | null>(null)
  const [isChangeRoleOpen, setIsChangeRoleOpen] = useState(false)
  const { removeMember, updateMember } = useWorkspaceMutations()
  const { members, membersLoading, membersError } = useWorkspaceMembers(
    workspace.id
  )
  const { revokeInvitation } = useWorkspaceInvitations(workspace.id, {
    enabled: user?.isPrivileged(),
  })

  const handleChangeRole = useCallback(
    async (newRole: WorkspaceRole) => {
      try {
        if (!selectedItem || selectedItem.status !== "active") {
          toast({
            title: "Cannot change role",
            description: "Can only change role for active members",
          })
          return
        }
        if (!selectedItem.user_id) return
        if (selectedItem.workspace_role === newRole) {
          toast({
            title: "No changes made",
            description: `User ${selectedItem.email} is already a ${newRole}`,
          })
          return
        }
        await updateMember({
          userId: selectedItem.user_id,
          workspaceId: workspace.id,
          requestBody: { role: newRole },
        })
      } catch (error) {
        console.log("Failed to change role", error)
      } finally {
        setIsChangeRoleOpen(false)
        setSelectedItem(null)
      }
    },
    [selectedItem, updateMember, workspace.id]
  )

  const handleRemoveMember = useCallback(async () => {
    if (!selectedItem) return
    try {
      if (selectedItem.status === "active" && selectedItem.user_id) {
        await removeMember(selectedItem.user_id)
      } else if (
        selectedItem.status === "pending" &&
        selectedItem.invitation_id
      ) {
        await revokeInvitation(selectedItem.invitation_id)
      }
    } catch (error) {
      console.log("Failed to remove", error)
    } finally {
      setSelectedItem(null)
    }
  }, [selectedItem, removeMember, revokeInvitation])

  return (
    <Dialog open={isChangeRoleOpen} onOpenChange={setIsChangeRoleOpen}>
      <AlertDialog
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setSelectedItem(null)
          }
        }}
      >
        <DataTable
          data={members ?? []}
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
                  {row.getValue<WorkspaceMemberOrInvitation["email"]>("email")}
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
                  {row.getValue<WorkspaceMemberOrInvitation["first_name"]>(
                    "first_name"
                  ) || "-"}
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
                  {row.getValue<WorkspaceMemberOrInvitation["last_name"]>(
                    "last_name"
                  ) || "-"}
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
                  {row.getValue<WorkspaceMemberOrInvitation["workspace_role"]>(
                    "workspace_role"
                  )}
                </div>
              ),
              enableSorting: true,
              enableHiding: false,
            },
            {
              accessorKey: "status",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Status"
                />
              ),
              cell: ({ row }) => {
                const status =
                  row.getValue<WorkspaceMemberOrInvitation["status"]>("status")
                return (
                  <Badge
                    variant={status === "active" ? "default" : "outline"}
                    className="text-xs"
                  >
                    {status === "active" ? "Active" : "Pending"}
                  </Badge>
                )
              },
              enableSorting: true,
              enableHiding: false,
            },
            {
              id: "actions",
              enableHiding: false,
              cell: ({ row }) => {
                const item = row.original
                const isMember = item.status === "active"
                const isInvitation = item.status === "pending"

                return (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" className="size-8 p-0">
                        <span className="sr-only">Open menu</span>
                        <DotsHorizontalIcon className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent>
                      {isMember && item.user_id && (
                        <DropdownMenuItem
                          onClick={() =>
                            navigator.clipboard.writeText(item.user_id!)
                          }
                        >
                          Copy user ID
                        </DropdownMenuItem>
                      )}
                      {isInvitation && item.invitation_id && (
                        <DropdownMenuItem
                          onClick={() =>
                            navigator.clipboard.writeText(item.invitation_id!)
                          }
                        >
                          Copy invitation ID
                        </DropdownMenuItem>
                      )}

                      {user?.isPrivileged() && (
                        <>
                          {isInvitation && item.token && (
                            <DropdownMenuItem
                              onClick={async () => {
                                try {
                                  const url = `${window.location.origin}/invitations/workspace/accept?token=${encodeURIComponent(item.token!)}`
                                  await navigator.clipboard.writeText(url)
                                  toast({
                                    title: "Copied",
                                    description:
                                      "Invitation link copied to clipboard",
                                  })
                                } catch {
                                  toast({
                                    title: "Error",
                                    description:
                                      "Failed to copy invitation link",
                                    variant: "destructive",
                                  })
                                }
                              }}
                            >
                              Copy invitation link
                            </DropdownMenuItem>
                          )}

                          {isMember && (
                            <DialogTrigger asChild>
                              <DropdownMenuItem
                                onClick={() => {
                                  setSelectedItem(item)
                                  setIsChangeRoleOpen(true)
                                }}
                              >
                                Change role
                              </DropdownMenuItem>
                            </DialogTrigger>
                          )}

                          <DropdownMenuSeparator />

                          <AlertDialogTrigger asChild>
                            <DropdownMenuItem
                              className="text-rose-500 focus:text-rose-600"
                              onClick={() => setSelectedItem(item)}
                            >
                              {isMember
                                ? "Remove from workspace"
                                : "Revoke invitation"}
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
            <AlertDialogTitle>
              {selectedItem?.status === "active"
                ? "Remove user"
                : "Revoke invitation"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {selectedItem?.status === "active"
                ? "Are you sure you want to remove this user from the workspace? This action cannot be undone."
                : `Are you sure you want to revoke the invitation for ${selectedItem?.email}? They will no longer be able to join this workspace with this invitation.`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={handleRemoveMember}
            >
              Confirm
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <ChangeUserRoleDialog
        selectedUser={selectedItem?.status === "active" ? selectedItem : null}
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
  selectedUser: WorkspaceMemberOrInvitation | null
  setOpen: (open: boolean) => void
  onConfirm: (role: WorkspaceRole) => Promise<void>
}) {
  const [newRole, setNewRole] = useState<WorkspaceRole>(
    selectedUser?.workspace_role || "editor"
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
        value={newRole}
        onValueChange={(value) => setNewRole(value as WorkspaceRole)}
      >
        <SelectTrigger>
          <SelectValue placeholder="Select a role" />
        </SelectTrigger>
        <SelectContent>
          {WorkspaceRoleEnum.map((role: WorkspaceRole) => (
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
        <Button onClick={() => onConfirm(newRole)}>Change role</Button>
      </DialogFooter>
    </DialogContent>
  )
}

const defaultToolbarProps: DataTableToolbarProps<WorkspaceMemberOrInvitation> =
  {
    filterProps: {
      placeholder: "Filter by email...",
      column: "email",
    },
  }
