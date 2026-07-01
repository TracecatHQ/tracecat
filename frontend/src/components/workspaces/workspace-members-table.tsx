"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useQueryClient } from "@tanstack/react-query"
import { useCallback, useEffect, useMemo, useState } from "react"
import {
  type WorkspaceRead,
  workspacesGetWorkspaceInvitationToken,
} from "@/client"
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
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { toast } from "@/components/ui/use-toast"
import { useWorkspaceInvitations } from "@/hooks/use-invitations"
import {
  useWorkspaceMembers,
  useWorkspaceMutations,
} from "@/hooks/use-workspace"
import { useAppInfo, useRbacRoles, useRbacUserAssignments } from "@/lib/hooks"

/**
 * A row in the workspace members table. Active members and pending invitations
 * come from separate endpoints and are merged client-side into this unified
 * shape, distinguished by `status`.
 */
type WorkspaceMemberRow = {
  status: "active" | "invited"
  email: string
  role_name: string
  // Active-member fields
  user_id?: string
  first_name?: string | null
  last_name?: string | null
  via_group?: boolean
  // Invitation fields
  invitation_id?: string
}

/**
 * A disabled dropdown item that still shows an explanatory tooltip.
 *
 * A disabled element does not emit the pointer or focus events Radix needs, so
 * it cannot itself be the tooltip trigger. Wrap the disabled item in a focusable
 * span and make the span the trigger so the explanation still shows on
 * hover/focus.
 */
function DisabledItemWithTooltip({
  label,
  tooltip,
}: {
  label: string
  tooltip: string
}) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span tabIndex={0}>
          <DropdownMenuItem disabled onSelect={(e) => e.preventDefault()}>
            {label}
          </DropdownMenuItem>
        </span>
      </TooltipTrigger>
      <TooltipContent>{tooltip}</TooltipContent>
    </Tooltip>
  )
}

export function WorkspaceMembersTable({
  workspace,
}: {
  workspace: WorkspaceRead
}) {
  const queryClient = useQueryClient()
  const canUpdateMembers = useScopeCheck("workspace:member:update")
  const canRemoveMembers = useScopeCheck("workspace:member:remove")
  const canInviteMembers = useScopeCheck("workspace:member:invite")
  const [selectedUser, setSelectedUser] = useState<WorkspaceMemberRow | null>(
    null
  )
  const [isChangeRoleOpen, setIsChangeRoleOpen] = useState(false)
  const { removeMember } = useWorkspaceMutations()
  const { appInfo } = useAppInfo()
  const {
    invitations,
    invitationsLoading,
    invitationsError,
    resendInvitation,
    revokeInvitation,
  } = useWorkspaceInvitations(workspace.id)
  const { members, membersLoading, membersError } = useWorkspaceMembers(
    workspace.id
  )

  // The table merges two independent endpoints, so reflect either source's
  // loading/error state rather than letting an invitation failure degrade the
  // table silently.
  const isLoading = membersLoading || invitationsLoading
  const error = membersError ?? invitationsError

  // The members list and the invitations list come from separate endpoints (no
  // extra DB round-trips versus a server-side merge). Combine them here into a
  // single table: active members first, then pending non-expired invitations.
  const rows = useMemo<WorkspaceMemberRow[]>(() => {
    const memberRows: WorkspaceMemberRow[] = (members ?? []).map((m) => ({
      status: "active",
      email: m.email,
      role_name: m.role_name,
      user_id: m.user_id,
      first_name: m.first_name,
      last_name: m.last_name,
      via_group: m.via_group,
    }))
    const now = Date.now()
    const invitationRows: WorkspaceMemberRow[] = (invitations ?? [])
      .filter(
        (inv) =>
          inv.status === "pending" &&
          (!inv.expires_at || new Date(inv.expires_at).getTime() > now)
      )
      .map((inv) => ({
        status: "invited",
        email: inv.email,
        role_name: inv.role_name,
        invitation_id: inv.id,
      }))
    return [...memberRows, ...invitationRows]
  }, [members, invitations])

  const copyInvitationLink = useCallback(
    async (invitationId: string) => {
      try {
        const { token } = await workspacesGetWorkspaceInvitationToken({
          workspaceId: workspace.id,
          invitationId,
        })
        const url = `${window.location.origin}/invitations/accept?token=${token}`
        await navigator.clipboard.writeText(url)
        toast({
          title: "Copied",
          description: "Invitation link copied to clipboard",
        })
      } catch {
        toast({
          title: "Error",
          description: "Failed to copy invitation link",
          variant: "destructive",
        })
      }
    },
    [workspace.id]
  )
  const {
    userAssignments,
    isLoading: userAssignmentsIsLoading,
    updateUserAssignment,
    updateUserAssignmentIsPending,
    createUserAssignment,
    createUserAssignmentIsPending,
  } = useRbacUserAssignments({
    workspaceId: workspace.id,
    enabled: isChangeRoleOpen,
  })

  const handleChangeRole = useCallback(
    async (roleId: string) => {
      try {
        if (!selectedUser?.user_id) {
          return toast({
            title: "No user selected",
            description: "Please select a user to change role",
          })
        }
        const selectedUserId = selectedUser.user_id
        if (!roleId) {
          return toast({
            title: "No role selected",
            description: "Please select a role before continuing.",
          })
        }
        if (userAssignmentsIsLoading) {
          return toast({
            title: "Role data is loading",
            description: "Wait a moment and try changing the role again.",
          })
        }
        // Find the existing RBAC assignment for this user in this workspace
        const existingAssignment = userAssignments?.find(
          (a) => a.user_id === selectedUserId && a.workspace_id === workspace.id
        )
        if (existingAssignment) {
          await updateUserAssignment({
            assignmentId: existingAssignment.id,
            role_id: roleId,
          })
        } else {
          // No existing assignment — create one
          await createUserAssignment({
            user_id: selectedUserId,
            role_id: roleId,
            workspace_id: workspace.id,
          })
        }
        await queryClient.invalidateQueries({
          queryKey: ["workspace", workspace.id, "members"],
        })
      } catch (error) {
        console.log("Failed to change role", error)
      } finally {
        setIsChangeRoleOpen(false)
        setSelectedUser(null)
      }
    },
    [
      selectedUser,
      userAssignments,
      workspace.id,
      updateUserAssignment,
      createUserAssignment,
      userAssignmentsIsLoading,
      queryClient,
    ]
  )

  const isRoleMutationPending =
    updateUserAssignmentIsPending || createUserAssignmentIsPending

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
          data={rows}
          isLoading={isLoading}
          error={error}
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
                <div className="flex items-center gap-2 text-xs">
                  {row.getValue<WorkspaceMemberRow["email"]>("email")}
                  {row.original.status === "invited" && (
                    <Badge variant="secondary" className="font-normal">
                      Invited
                    </Badge>
                  )}
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
                  {row.getValue<WorkspaceMemberRow["first_name"]>(
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
                  {row.getValue<WorkspaceMemberRow["last_name"]>("last_name") ||
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
                <div className="flex items-center gap-2 text-xs capitalize">
                  {row.getValue<string>("role_name")}
                  {row.original.via_group && (
                    <Tooltip>
                      <TooltipTrigger asChild>
                        <Badge variant="secondary" className="font-normal">
                          Via group
                        </Badge>
                      </TooltipTrigger>
                      <TooltipContent>
                        Access granted through a group. Manage this role from
                        the group's settings.
                      </TooltipContent>
                    </Tooltip>
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
                const member = row.original
                const isInvited = member.status === "invited"

                if (isInvited) {
                  const invitationId = member.invitation_id
                  // Resend/copy require invite scope; revoke requires remove
                  // scope (the API gates them separately). Render the menu if
                  // either action is available.
                  if (
                    !invitationId ||
                    (!canInviteMembers && !canRemoveMembers)
                  ) {
                    return null
                  }
                  return (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" className="size-8 p-0">
                          <span className="sr-only">Open menu</span>
                          <DotsHorizontalIcon className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        {canInviteMembers && appInfo?.smtp_configured && (
                          <DropdownMenuItem
                            onSelect={() => resendInvitation(invitationId)}
                          >
                            Resend invitation
                          </DropdownMenuItem>
                        )}
                        {canInviteMembers && (
                          <DropdownMenuItem
                            onSelect={() => copyInvitationLink(invitationId)}
                          >
                            Copy invitation link
                          </DropdownMenuItem>
                        )}
                        {canRemoveMembers && (
                          <DropdownMenuItem
                            className="text-rose-500 focus:text-rose-600"
                            onSelect={() => revokeInvitation(invitationId)}
                          >
                            Revoke invitation
                          </DropdownMenuItem>
                        )}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )
                }

                // Managed via the group, and the backend rejects these edits —
                // disable the row actions rather than offer a guaranteed failure.
                const viaGroup = member.via_group ?? false
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
                          member.user_id &&
                          navigator.clipboard.writeText(member.user_id)
                        }
                      >
                        Copy user ID
                      </DropdownMenuItem>

                      {canUpdateMembers &&
                        (viaGroup ? (
                          <DisabledItemWithTooltip
                            label="Change role"
                            tooltip="Role is managed through the group."
                          />
                        ) : (
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
                        ))}

                      {canRemoveMembers &&
                        (viaGroup ? (
                          <DisabledItemWithTooltip
                            label="Remove from workspace"
                            tooltip="Remove the user from the group to revoke access."
                          />
                        ) : (
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
                        ))}
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
                if (selectedUser?.user_id) {
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
      <ChangeUserRoleDialog
        open={isChangeRoleOpen}
        selectedUser={selectedUser}
        isSubmitting={isRoleMutationPending || userAssignmentsIsLoading}
        setOpen={setIsChangeRoleOpen}
        onConfirm={handleChangeRole}
      />
    </Dialog>
  )
}

function ChangeUserRoleDialog({
  open,
  selectedUser,
  isSubmitting,
  setOpen,
  onConfirm,
}: {
  open: boolean
  selectedUser: WorkspaceMemberRow | null
  isSubmitting: boolean
  setOpen: (open: boolean) => void
  onConfirm: (roleId: string) => void
}) {
  const { roles, isLoading: rolesIsLoading } = useRbacRoles({
    enabled: open,
  })
  const workspaceRoles = useMemo(
    () => roles.filter((r) => !r.slug || r.slug.startsWith("workspace-")),
    [roles]
  )
  const [selectedRoleId, setSelectedRoleId] = useState<string>("")

  useEffect(() => {
    if (!open) {
      setSelectedRoleId("")
      return
    }
    const match = workspaceRoles.find((r) => r.name === selectedUser?.role_name)
    setSelectedRoleId(match?.id ?? "")
  }, [open, selectedUser, workspaceRoles])

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Change user role</DialogTitle>
        <DialogDescription>
          Select a new role for {selectedUser?.email}
        </DialogDescription>
      </DialogHeader>
      <Select
        value={selectedRoleId}
        onValueChange={(value) => setSelectedRoleId(value)}
      >
        <SelectTrigger disabled={rolesIsLoading}>
          <SelectValue placeholder="Select a role" />
        </SelectTrigger>
        <SelectContent>
          {workspaceRoles.map((role) => (
            <SelectItem key={role.id} value={role.id}>
              {role.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <DialogFooter>
        <Button variant="outline" onClick={() => setOpen(false)}>
          Cancel
        </Button>
        <Button
          disabled={!selectedRoleId || rolesIsLoading || isSubmitting}
          onClick={() => onConfirm(selectedRoleId)}
        >
          Change role
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}

const defaultToolbarProps: DataTableToolbarProps<WorkspaceMemberRow> = {
  filterProps: {
    placeholder: "Filter users by email...",
    column: "email",
  },
}
