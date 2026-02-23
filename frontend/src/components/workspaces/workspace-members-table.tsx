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
  useWorkspaceInvitations,
  useWorkspaceMembers,
  useWorkspaceMutations,
} from "@/hooks/use-workspace"
import { useRbacRoles, useRbacUserAssignments } from "@/lib/hooks"

export function WorkspaceMembersTable({
  workspace,
}: {
  workspace: WorkspaceRead
}) {
  const queryClient = useQueryClient()
  const canManageMembers = useScopeCheck("workspace:member:update")
  const canRemoveMembers = useScopeCheck("workspace:member:remove")
  const [selectedMember, setSelectedMember] = useState<WorkspaceMember | null>(
    null
  )
  const [isChangeRoleOpen, setIsChangeRoleOpen] = useState(false)
  const { removeMember } = useWorkspaceMutations()
  const { revokeInvitation } = useWorkspaceInvitations(workspace.id)
  const { members, membersLoading, membersError } = useWorkspaceMembers(
    workspace.id
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

  const [copied, setCopied] = useState<string | null>(null)

  async function handleCopyInviteLink(token: string) {
    const link = `${window.location.origin}/invitations/workspace/accept?token=${token}`
    await navigator.clipboard.writeText(link)
    setCopied(token)
    setTimeout(() => setCopied(null), 2000)
  }

  const handleChangeRole = useCallback(
    async (roleId: string) => {
      try {
        if (!selectedMember?.user_id) {
          return toast({
            title: "No user selected",
            description: "Please select a user to change role",
          })
        }
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
          (a) =>
            a.user_id === selectedMember.user_id &&
            a.workspace_id === workspace.id
        )
        if (existingAssignment) {
          await updateUserAssignment({
            assignmentId: existingAssignment.id,
            role_id: roleId,
          })
        } else {
          await createUserAssignment({
            user_id: selectedMember.user_id,
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
        setSelectedMember(null)
      }
    },
    [
      selectedMember,
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
            setSelectedMember(null)
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
                <div className="text-xs capitalize">
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
                const memberStatus = row.getValue<string>("status")
                const variant =
                  memberStatus === "active"
                    ? "default"
                    : memberStatus === "invited"
                      ? "outline"
                      : "secondary"
                return (
                  <Badge variant={variant} className="text-xs capitalize">
                    {memberStatus}
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
                const isInvited = member.status === "invited"

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
                          {member.token && (
                            <DropdownMenuItem
                              onClick={() =>
                                handleCopyInviteLink(member.token as string)
                              }
                            >
                              {copied === member.token ? (
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
                                onClick={() => setSelectedMember(member)}
                              >
                                Revoke invitation
                              </DropdownMenuItem>
                            </AlertDialogTrigger>
                          )}
                        </>
                      ) : (
                        <>
                          {member.user_id && (
                            <DropdownMenuItem
                              onClick={() =>
                                navigator.clipboard.writeText(
                                  member.user_id as string
                                )
                              }
                            >
                              Copy user ID
                            </DropdownMenuItem>
                          )}
                          {canManageMembers && (
                            <DialogTrigger asChild>
                              <DropdownMenuItem
                                onClick={() => {
                                  setSelectedMember(member)
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
                                onClick={() => setSelectedMember(member)}
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
              {selectedMember?.status === "invited"
                ? "Revoke invitation"
                : "Remove user"}
            </AlertDialogTitle>
            <AlertDialogDescription>
              {selectedMember?.status === "invited"
                ? `Are you sure you want to revoke the invitation for ${selectedMember?.email}?`
                : "Are you sure you want to remove this user from the workspace? This action cannot be undone."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={async () => {
                if (selectedMember) {
                  try {
                    if (
                      selectedMember.status === "invited" &&
                      selectedMember.invitation_id
                    ) {
                      await revokeInvitation(selectedMember.invitation_id)
                    } else if (selectedMember.user_id) {
                      await removeMember(selectedMember.user_id)
                    }
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
                setSelectedMember(null)
              }}
            >
              Confirm
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      <ChangeUserRoleDialog
        open={isChangeRoleOpen}
        selectedUser={selectedMember}
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
  selectedUser: WorkspaceMember | null
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

const defaultToolbarProps: DataTableToolbarProps<WorkspaceMember> = {
  filterProps: {
    placeholder: "Filter by email...",
    column: "email",
  },
}
