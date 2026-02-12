"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useCallback, useMemo, useState } from "react"
import type {
  WorkspaceMember,
  WorkspaceMembershipRead,
  WorkspaceRead,
  WorkspaceRole,
} from "@/client"
import { workspacesGetInvitationToken } from "@/client"
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
  useCurrentUserRole,
  useWorkspaceMembers,
  useWorkspaceMutations,
} from "@/hooks/use-workspace"
import { useWorkspaceInvitations } from "@/lib/hooks"
import { WorkspaceRoleEnum } from "@/lib/workspace"

// Combined type for both members and pending invitations
type MemberOrInvitation = {
  id: string
  email: string
  first_name: string | null
  last_name: string | null
  workspace_role: WorkspaceRole
  status: "active" | "pending"
  // Original data for actions
  _type: "member" | "invitation"
  _original:
    | WorkspaceMember
    | { id: string; email: string; role: WorkspaceRole }
}

export function WorkspaceMembersTable({
  workspace,
}: {
  workspace: WorkspaceRead
}) {
  const { user } = useAuth()
  const [selectedItem, setSelectedItem] = useState<MemberOrInvitation | null>(
    null
  )
  const [isChangeRoleOpen, setIsChangeRoleOpen] = useState(false)
  const { role } = useCurrentUserRole(workspace.id)
  const { removeMember, updateMember } = useWorkspaceMutations()
  const { members, membersLoading, membersError } = useWorkspaceMembers(
    workspace.id
  )
  const {
    invitations,
    isLoading: invitationsLoading,
    revokeInvitation,
  } = useWorkspaceInvitations(workspace.id)

  // Combine members and pending invitations into a single list
  const combinedData = useMemo<MemberOrInvitation[]>(() => {
    const memberRows: MemberOrInvitation[] = (members ?? []).map((m) => ({
      id: m.user_id,
      email: m.email,
      first_name: m.first_name,
      last_name: m.last_name,
      workspace_role: m.workspace_role,
      status: "active" as const,
      _type: "member" as const,
      _original: m,
    }))

    const pendingInvitations =
      invitations?.filter((inv) => inv.status === "pending") ?? []
    const invitationRows: MemberOrInvitation[] = pendingInvitations.map(
      (inv) => ({
        id: inv.id,
        email: inv.email,
        first_name: null,
        last_name: null,
        workspace_role: inv.role,
        status: "pending" as const,
        _type: "invitation" as const,
        _original: { id: inv.id, email: inv.email, role: inv.role },
      })
    )

    return [...memberRows, ...invitationRows]
  }, [members, invitations])

  const handleChangeRole = useCallback(
    async (newRole: WorkspaceRole) => {
      try {
        if (!selectedItem || selectedItem._type !== "member") {
          return toast({
            title: "Cannot change role",
            description: "Can only change role for active members",
          })
        }
        const member = selectedItem._original as WorkspaceMember
        if (member.workspace_role === newRole) {
          return toast({
            title: "No changes made",
            description: `User ${member.email} is already a ${newRole}`,
          })
        }
        await updateMember({
          userId: member.user_id,
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
      if (selectedItem._type === "member") {
        const member = selectedItem._original as WorkspaceMember
        await removeMember(member.user_id)
      } else {
        const invitation = selectedItem._original as { id: string }
        await revokeInvitation(invitation.id)
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
          data={combinedData}
          isLoading={membersLoading || invitationsLoading}
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
                  {row.getValue<MemberOrInvitation["email"]>("email")}
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
                  {row.getValue<MemberOrInvitation["first_name"]>(
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
                  {row.getValue<MemberOrInvitation["last_name"]>("last_name") ||
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
                  {row.getValue<MemberOrInvitation["workspace_role"]>(
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
                  row.getValue<MemberOrInvitation["status"]>("status")
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
                const isMember = item._type === "member"
                const isInvitation = item._type === "invitation"

                return (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" className="size-8 p-0">
                        <span className="sr-only">Open menu</span>
                        <DotsHorizontalIcon className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent>
                      {isMember && (
                        <DropdownMenuItem
                          onClick={() => navigator.clipboard.writeText(item.id)}
                        >
                          Copy user ID
                        </DropdownMenuItem>
                      )}
                      {isInvitation && (
                        <DropdownMenuItem
                          onClick={() => navigator.clipboard.writeText(item.id)}
                        >
                          Copy invitation ID
                        </DropdownMenuItem>
                      )}

                      {user?.isPrivileged({
                        role,
                      } as WorkspaceMembershipRead) && (
                        <>
                          {isInvitation && (
                            <DropdownMenuItem
                              onClick={async () => {
                                try {
                                  const { token } =
                                    await workspacesGetInvitationToken({
                                      workspaceId: workspace.id,
                                      invitationId: item.id,
                                    })
                                  const url = `${window.location.origin}/invitations/workspace/accept?token=${encodeURIComponent(token)}`
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
              {selectedItem?._type === "member"
                ? "Remove user"
                : "Revoke invitation"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {selectedItem?._type === "member"
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
        selectedUser={
          selectedItem?._type === "member"
            ? (selectedItem._original as WorkspaceMember)
            : null
        }
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

const defaultToolbarProps: DataTableToolbarProps<MemberOrInvitation> = {
  filterProps: {
    placeholder: "Filter by email...",
    column: "email",
  },
}
