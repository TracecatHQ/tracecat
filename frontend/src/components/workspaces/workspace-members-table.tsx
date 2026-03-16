"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useQueryClient } from "@tanstack/react-query"
import { Check, Copy } from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"
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
import { toast } from "@/components/ui/use-toast"
import {
  buildInvitationAcceptUrl,
  getInvitationToken,
  useInvitations,
} from "@/hooks/use-invitations"
import {
  useWorkspaceMembers,
  useWorkspaceMutations,
} from "@/hooks/use-workspace"
import { useRbacRoles, useRbacUserAssignments } from "@/lib/hooks"

type ActiveWorkspaceMember = WorkspaceMember & {
  kind: "member"
  status: "active"
}

type InvitedWorkspaceMember = {
  kind: "invitation"
  status: "invited"
  invitation_id: string
  email: string
  role_name: string
  first_name: null
  last_name: null
}

type WorkspaceTableRow = ActiveWorkspaceMember | InvitedWorkspaceMember

export function WorkspaceMembersTable({
  workspace,
}: {
  workspace: WorkspaceRead
}) {
  const queryClient = useQueryClient()
  const canInviteMembers = useScopeCheck("workspace:member:invite")
  const canManageMembers = useScopeCheck("workspace:member:update")
  const canRemoveMembers = useScopeCheck("workspace:member:remove")
  const [selectedRow, setSelectedRow] = useState<WorkspaceTableRow | null>(null)
  const [isChangeRoleOpen, setIsChangeRoleOpen] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  const { removeMember } = useWorkspaceMutations()
  const { revokeInvitation } = useInvitations({ workspaceId: workspace.id })
  const { members, membersLoading, membersError } = useWorkspaceMembers(
    workspace.id
  )
  const { invitations, invitationsLoading, invitationsError } = useInvitations({
    workspaceId: workspace.id,
    status: "pending",
  })
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

  const rows = useMemo<WorkspaceTableRow[]>(() => {
    const activeRows =
      members?.map((member) => ({
        ...member,
        kind: "member" as const,
        status: "active" as const,
      })) ?? []
    const invitedRows =
      invitations
        ?.filter((invitation) => invitation.workspace_id === workspace.id)
        .map((invitation) => ({
          kind: "invitation" as const,
          status: "invited" as const,
          invitation_id: invitation.id,
          email: invitation.email,
          role_name: invitation.role_name,
          first_name: null,
          last_name: null,
        })) ?? []

    return [...activeRows, ...invitedRows]
  }, [invitations, members, workspace.id])

  async function handleCopyInviteLink(invitationId: string) {
    try {
      const token = await getInvitationToken(invitationId)
      const link = `${window.location.origin}${buildInvitationAcceptUrl(token)}`
      await navigator.clipboard.writeText(link)
      setCopied(invitationId)
      setTimeout(() => setCopied(null), 2000)
    } catch {
      toast({
        title: "Failed to copy invitation link",
        description: "Could not retrieve the invitation token.",
        variant: "destructive",
      })
    }
  }

  const handleChangeRole = useCallback(
    async (roleId: string) => {
      try {
        if (!selectedRow || selectedRow.kind !== "member") {
          toast({
            title: "No user selected",
            description: "Please select a user to change role.",
          })
          return
        }
        if (!roleId) {
          toast({
            title: "No role selected",
            description: "Please select a role before continuing.",
          })
          return
        }
        if (userAssignmentsIsLoading) {
          toast({
            title: "Role data is loading",
            description: "Wait a moment and try changing the role again.",
          })
          return
        }

        const existingAssignment = userAssignments?.find(
          (assignment) =>
            assignment.user_id === selectedRow.user_id &&
            assignment.workspace_id === workspace.id
        )

        if (existingAssignment) {
          await updateUserAssignment({
            assignmentId: existingAssignment.id,
            role_id: roleId,
          })
        } else {
          await createUserAssignment({
            user_id: selectedRow.user_id,
            role_id: roleId,
            workspace_id: workspace.id,
          })
        }

        await queryClient.invalidateQueries({
          queryKey: ["workspace", workspace.id, "members"],
        })
      } catch (error) {
        console.error("Failed to change role", error)
      } finally {
        setIsChangeRoleOpen(false)
        setSelectedRow(null)
      }
    },
    [
      createUserAssignment,
      queryClient,
      selectedRow,
      updateUserAssignment,
      userAssignments,
      userAssignmentsIsLoading,
      workspace.id,
    ]
  )

  const isRoleMutationPending =
    updateUserAssignmentIsPending || createUserAssignmentIsPending

  return (
    <Dialog open={isChangeRoleOpen} onOpenChange={setIsChangeRoleOpen}>
      <AlertDialog
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setSelectedRow(null)
          }
        }}
      >
        <DataTable
          data={rows}
          isLoading={membersLoading || invitationsLoading}
          error={membersError ?? invitationsError}
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
                  {row.getValue<WorkspaceTableRow["email"]>("email")}
                </div>
              ),
              enableSorting: true,
              enableHiding: false,
            },
            {
              id: "name",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Name"
                />
              ),
              cell: ({ row }) => {
                const first = row.original.first_name
                const last = row.original.last_name
                const name = [first, last].filter(Boolean).join(" ")
                return <div className="text-xs">{name || "-"}</div>
              },
              enableSorting: false,
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
                <div className="text-xs">
                  {row.getValue<string>("role_name")}
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
                const status = row.original.status
                return (
                  <Badge
                    variant={status === "invited" ? "outline" : "default"}
                    className="text-xs capitalize"
                  >
                    {status}
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
                const member = row.original
                const isInvited = member.kind === "invitation"

                return (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" className="size-8 p-0">
                        <span className="sr-only">Open menu</span>
                        <DotsHorizontalIcon className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent>
                      {isInvited ? (
                        <>
                          {canInviteMembers && (
                            <DropdownMenuItem
                              onClick={() =>
                                handleCopyInviteLink(member.invitation_id)
                              }
                            >
                              {copied === member.invitation_id ? (
                                <Check className="mr-2 size-4" />
                              ) : (
                                <Copy className="mr-2 size-4" />
                              )}
                              Copy invitation link
                            </DropdownMenuItem>
                          )}
                          {canRemoveMembers && (
                            <AlertDialogTrigger asChild>
                              <DropdownMenuItem
                                className="text-rose-500 focus:text-rose-600"
                                onClick={() => setSelectedRow(member)}
                              >
                                Revoke invitation
                              </DropdownMenuItem>
                            </AlertDialogTrigger>
                          )}
                        </>
                      ) : (
                        <>
                          <DropdownMenuItem
                            onClick={() =>
                              navigator.clipboard.writeText(member.user_id)
                            }
                          >
                            Copy user ID
                          </DropdownMenuItem>
                          {canManageMembers && (
                            <DialogTrigger asChild>
                              <DropdownMenuItem
                                onClick={() => {
                                  setSelectedRow(member)
                                  setIsChangeRoleOpen(true)
                                }}
                              >
                                Change role
                              </DropdownMenuItem>
                            </DialogTrigger>
                          )}
                          {canRemoveMembers && (
                            <AlertDialogTrigger asChild>
                              <DropdownMenuItem
                                className="text-rose-500 focus:text-rose-600"
                                onClick={() => setSelectedRow(member)}
                              >
                                Remove from workspace
                              </DropdownMenuItem>
                            </AlertDialogTrigger>
                          )}
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
              {selectedRow?.kind === "invitation"
                ? "Revoke invitation"
                : "Remove user"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {selectedRow?.kind === "invitation"
                ? `Are you sure you want to revoke the invitation for ${selectedRow.email}?`
                : "Are you sure you want to remove this user from the workspace? This action cannot be undone."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={async () => {
                if (!selectedRow) {
                  return
                }
                try {
                  if (selectedRow.kind === "invitation") {
                    await revokeInvitation(selectedRow.invitation_id)
                  } else {
                    await removeMember(selectedRow.user_id)
                  }
                } catch (error) {
                  toast({
                    title: "Failed to remove member",
                    description:
                      error instanceof Error
                        ? error.message
                        : "The request could not be completed.",
                    variant: "destructive",
                  })
                } finally {
                  setSelectedRow(null)
                }
              }}
            >
              Confirm
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <ChangeUserRoleDialog
        open={isChangeRoleOpen}
        selectedUser={selectedRow?.kind === "member" ? selectedRow : null}
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
  selectedUser: ActiveWorkspaceMember | null
  isSubmitting: boolean
  setOpen: (open: boolean) => void
  onConfirm: (roleId: string) => Promise<unknown>
}) {
  const { roles, isLoading: rolesIsLoading } = useRbacRoles({
    enabled: open,
  })
  const workspaceRoles = useMemo(
    () =>
      roles.filter((role) => !role.slug || role.slug.startsWith("workspace-")),
    [roles]
  )
  const [selectedRoleId, setSelectedRoleId] = useState("")

  useEffect(() => {
    if (!open) {
      setSelectedRoleId("")
      return
    }
    const match = workspaceRoles.find(
      (role) => role.name === selectedUser?.role_name
    )
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

const defaultToolbarProps: DataTableToolbarProps<WorkspaceTableRow> = {
  filterProps: {
    placeholder: "Filter by email...",
    column: "email",
  },
}
