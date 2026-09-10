"use client"

import { DialogTrigger } from "@radix-ui/react-dialog"
import { DotsHorizontalIcon, PlusIcon } from "@radix-ui/react-icons"
import { FolderIcon, GlobeIcon, Trash2Icon } from "lucide-react"
import { useSearchParams } from "next/navigation"
import { useState } from "react"
import { invitationsGetInvitationToken, type OrgMemberRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { InviteMemberDialogButton } from "@/components/organization/invite-member-dialog"
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
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { getRelativeTime } from "@/lib/event-history"
import {
  useOrgMembers,
  useRbacRoles,
  useRbacUserAssignments,
  useWorkspaceManager,
} from "@/lib/hooks"
import { invitationGrantsSummary } from "@/lib/invitations"
import { toast } from "../ui/use-toast"

export function OrgMembersTable() {
  const [selectedMember, setSelectedMember] = useState<OrgMemberRead | null>(
    null
  )
  const [isChangeRoleOpen, setIsChangeRoleOpen] = useState(false)
  const [removeConfirmationEmail, setRemoveConfirmationEmail] = useState("")
  const canInviteMembers = useScopeCheck("org:member:invite") === true
  const canRemoveMembers = useScopeCheck("org:member:remove") === true
  const canReadRbac = useScopeCheck("org:rbac:read") === true
  const { orgMembers, deleteOrgMember, revokeInvitation } = useOrgMembers()
  const { roles } = useRbacRoles()
  const { workspaces } = useWorkspaceManager()
  const searchParams = useSearchParams()
  const inviteWorkspaceId = searchParams.get("inviteWorkspace")

  // Invited rows show what the invitation will confer; members show their role.
  const roleText = (member: OrgMemberRead): string =>
    member.invitation_id
      ? invitationGrantsSummary(
          { grants: member.grants ?? [] },
          roles,
          workspaces ?? []
        )
      : member.role_name

  const handleRemoveMember = async () => {
    if (
      selectedMember?.user_id &&
      removeConfirmationEmail === selectedMember.email
    ) {
      try {
        await deleteOrgMember({
          userId: selectedMember.user_id,
        })
      } catch (error) {
        console.error("Failed to remove member", error)
      } finally {
        setSelectedMember(null)
        setRemoveConfirmationEmail("")
      }
    }
  }

  const handleRevokeInvitation = async () => {
    if (selectedMember?.invitation_id) {
      try {
        await revokeInvitation(selectedMember.invitation_id)
      } catch {
        // Error handled in hook
      } finally {
        setSelectedMember(null)
        setRemoveConfirmationEmail("")
      }
    }
  }

  const toolbarProps: DataTableToolbarProps<OrgMemberRead> = {
    ...defaultToolbarProps,
    actions: (
      <InviteMemberDialogButton initialWorkspaceId={inviteWorkspaceId} />
    ),
  }

  return (
    <div className="space-y-4">
      <Dialog open={isChangeRoleOpen} onOpenChange={setIsChangeRoleOpen}>
        <AlertDialog
          onOpenChange={(isOpen) => {
            if (!isOpen) {
              setSelectedMember(null)
              setRemoveConfirmationEmail("")
            }
          }}
        >
          <DataTable
            data={orgMembers ?? []}
            initialSortingState={[{ id: "email", desc: false }]}
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
                    {row.getValue<OrgMemberRead["email"]>("email")}
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
                  const { first_name, last_name } = row.original
                  const name = [first_name, last_name].filter(Boolean).join(" ")
                  return <div className="text-xs">{name || "-"}</div>
                },
                enableSorting: false,
                enableHiding: false,
              },
              {
                id: "role_name",
                accessorFn: roleText,
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Role"
                  />
                ),
                cell: ({ row }) => (
                  <div className="text-xs">{roleText(row.original)}</div>
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
                  const memberStatus =
                    row.getValue<OrgMemberRead["status"]>("status")
                  const variant =
                    memberStatus === "active"
                      ? "default"
                      : memberStatus === "inactive"
                        ? "secondary"
                        : "outline"
                  return (
                    <Badge variant={variant}>
                      {memberStatus.charAt(0).toUpperCase() +
                        memberStatus.slice(1)}
                    </Badge>
                  )
                },
                enableSorting: true,
                enableHiding: false,
              },
              {
                accessorKey: "last_login_at",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Last login"
                  />
                ),
                cell: ({ row }) => {
                  const lastLoginAt =
                    row.getValue<OrgMemberRead["last_login_at"]>(
                      "last_login_at"
                    )
                  if (!lastLoginAt) {
                    return <div className="text-xs">-</div>
                  }
                  const date = new Date(lastLoginAt)
                  const ago = getRelativeTime(date)
                  return (
                    <div className="space-x-2 text-xs">
                      <span>{date.toLocaleString()}</span>
                      <span className="text-muted-foreground">({ago})</span>
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
                      <DropdownMenuContent align="end">
                        {isInvited ? (
                          <>
                            {canInviteMembers && (
                              <>
                                <DropdownMenuItem
                                  onSelect={async () => {
                                    if (!member.invitation_id) return
                                    try {
                                      const { token } =
                                        await invitationsGetInvitationToken({
                                          invitationId: member.invitation_id,
                                        })
                                      const url = `${window.location.origin}/invitations/accept?token=${token}`
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
                                <DropdownMenuSeparator />
                                <AlertDialogTrigger asChild>
                                  <DropdownMenuItem
                                    className="text-rose-500 focus:text-rose-600"
                                    onSelect={() => setSelectedMember(member)}
                                  >
                                    Revoke invitation
                                  </DropdownMenuItem>
                                </AlertDialogTrigger>
                              </>
                            )}
                          </>
                        ) : (
                          <>
                            <DropdownMenuItem
                              onClick={() => {
                                if (member.user_id) {
                                  navigator.clipboard.writeText(member.user_id)
                                }
                              }}
                            >
                              Copy user ID
                            </DropdownMenuItem>
                            {(canReadRbac || canRemoveMembers) && (
                              <DropdownMenuGroup>
                                {canReadRbac && (
                                  <DialogTrigger asChild>
                                    <DropdownMenuItem
                                      onClick={() => {
                                        setSelectedMember(member)
                                        setIsChangeRoleOpen(true)
                                      }}
                                    >
                                      Manage roles
                                    </DropdownMenuItem>
                                  </DialogTrigger>
                                )}
                                {canRemoveMembers && (
                                  <>
                                    {canReadRbac && <DropdownMenuSeparator />}
                                    <AlertDialogTrigger asChild>
                                      <DropdownMenuItem
                                        className="text-rose-500 focus:text-rose-600"
                                        onClick={() => {
                                          setSelectedMember(member)
                                          setRemoveConfirmationEmail("")
                                        }}
                                      >
                                        Remove from organization
                                      </DropdownMenuItem>
                                    </AlertDialogTrigger>
                                  </>
                                )}
                              </DropdownMenuGroup>
                            )}
                          </>
                        )}
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )
                },
              },
            ]}
            toolbarProps={toolbarProps}
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
                  ? `Are you sure you want to revoke the invitation for ${selectedMember?.email}? They will no longer be able to join this organization with this invitation.`
                  : `Are you sure you want to remove ${selectedMember?.email} from the organization? This revokes their sessions and removes their organization access.`}
              </AlertDialogDescription>
            </AlertDialogHeader>
            {selectedMember?.status !== "invited" && selectedMember && (
              <div className="flex flex-col gap-2">
                <Label htmlFor="remove-org-member-confirmation">
                  Type the user email to confirm
                </Label>
                <Input
                  id="remove-org-member-confirmation"
                  value={removeConfirmationEmail}
                  onChange={(event) =>
                    setRemoveConfirmationEmail(event.target.value)
                  }
                  placeholder={selectedMember.email}
                  autoComplete="off"
                  autoCapitalize="none"
                  spellCheck={false}
                />
              </div>
            )}
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
                disabled={
                  selectedMember?.status !== "invited" &&
                  removeConfirmationEmail !== selectedMember?.email
                }
                onClick={
                  selectedMember?.status === "invited"
                    ? handleRevokeInvitation
                    : handleRemoveMember
                }
              >
                {selectedMember?.status === "invited" ? "Revoke" : "Confirm"}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
        {selectedMember && (
          <ManageUserRolesDialog
            member={selectedMember}
            onOpenChange={setIsChangeRoleOpen}
          />
        )}
      </Dialog>
    </div>
  )
}

function ManageUserRolesDialog({
  member,
  onOpenChange,
}: {
  member: OrgMemberRead
  onOpenChange: (open: boolean) => void
}) {
  const [roleId, setRoleId] = useState("")
  const [workspaceId, setWorkspaceId] = useState<string>("org-wide")
  const [pendingOrgRemovalId, setPendingOrgRemovalId] = useState<string | null>(
    null
  )
  const userId = member.user_id ?? undefined

  const {
    userAssignments,
    createUserAssignment,
    createUserAssignmentIsPending,
    updateUserAssignment,
    updateUserAssignmentIsPending,
    deleteUserAssignment,
    deleteUserAssignmentIsPending,
  } = useRbacUserAssignments({ userId })
  const { roles } = useRbacRoles()
  const { workspaces } = useWorkspaceManager()
  const canReadRbac = useScopeCheck("org:rbac:read") === true
  const canCreateAssignment = useScopeCheck("org:rbac:create") === true
  const canUpdateAssignment = useScopeCheck("org:rbac:update") === true
  const canDeleteAssignment = useScopeCheck("org:rbac:delete") === true

  // A user holds at most one org-wide assignment, so changing that role is an
  // update: creating a second one would conflict.
  const existingOrgAssignment = userAssignments.find(
    (assignment) => assignment.workspace_id == null
  )
  const isUpdate = workspaceId === "org-wide" && Boolean(existingOrgAssignment)
  const canSubmit = isUpdate ? canUpdateAssignment : canCreateAssignment

  const handleAddRole = async () => {
    if (!roleId || !userId) return
    if (workspaceId === "org-wide" && existingOrgAssignment) {
      await updateUserAssignment({
        assignmentId: existingOrgAssignment.id,
        role_id: roleId,
      })
    } else {
      await createUserAssignment({
        user_id: userId,
        role_id: roleId,
        workspace_id: workspaceId === "org-wide" ? null : workspaceId,
      })
    }
    setRoleId("")
    setWorkspaceId("org-wide")
  }

  // Removing an org-wide role can drop the user from the member list, so
  // confirm it; workspace-scoped removals stay one click.
  const handleRemoveRole = async (assignmentId: string) => {
    const assignment = userAssignments.find((a) => a.id === assignmentId)
    if (assignment?.workspace_id == null) {
      setPendingOrgRemovalId(assignmentId)
      return
    }
    await deleteUserAssignment(assignmentId)
  }

  const handleConfirmOrgRemoval = async () => {
    if (!pendingOrgRemovalId) return
    await deleteUserAssignment(pendingOrgRemovalId)
    setPendingOrgRemovalId(null)
  }

  return (
    <DialogContent className="max-w-lg">
      <DialogHeader>
        <DialogTitle>Manage roles - {member.email}</DialogTitle>
        <DialogDescription>
          Assign or remove roles for this user. Roles grant permissions within
          workspaces or across the organization.
        </DialogDescription>
      </DialogHeader>
      <div className="space-y-4 py-4">
        {(canCreateAssignment || canUpdateAssignment) && (
          <div className="space-y-2">
            <Label>Add role assignment</Label>
            <div className="flex gap-2">
              <Select value={roleId} onValueChange={setRoleId}>
                <SelectTrigger className="flex-1">
                  <SelectValue placeholder="Select a role" />
                </SelectTrigger>
                <SelectContent>
                  {roles.map((role) => (
                    <SelectItem key={role.id} value={role.id}>
                      {role.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select value={workspaceId} onValueChange={setWorkspaceId}>
                <SelectTrigger className="w-[180px]">
                  <SelectValue placeholder="Scope" />
                </SelectTrigger>
                <SelectContent>
                  <SelectItem value="org-wide">
                    <div className="flex items-center gap-2">
                      <GlobeIcon className="size-4 text-blue-500" />
                      Organization
                    </div>
                  </SelectItem>
                  {workspaces?.map((workspace) => (
                    <SelectItem key={workspace.id} value={workspace.id}>
                      <div className="flex items-center gap-2">
                        <FolderIcon className="size-4 text-muted-foreground" />
                        {workspace.name}
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                type="button"
                onClick={handleAddRole}
                disabled={
                  !roleId ||
                  createUserAssignmentIsPending ||
                  updateUserAssignmentIsPending ||
                  !canSubmit
                }
              >
                <PlusIcon className="size-4" />
              </Button>
            </div>
          </div>
        )}

        <div className="space-y-2">
          <Label>Current role assignments ({userAssignments.length})</Label>
          <ScrollArea className="h-[200px] rounded-md border">
            {userAssignments.length > 0 ? (
              <div className="space-y-2 p-4">
                {userAssignments.map((assignment) => (
                  <div
                    key={assignment.id}
                    className="flex items-center justify-between rounded-md border p-2"
                  >
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center gap-2">
                        <Badge variant="secondary">
                          {assignment.role_name}
                        </Badge>
                      </div>
                      <span className="flex items-center gap-1 text-xs text-muted-foreground">
                        {assignment.workspace_name ? (
                          <>
                            <FolderIcon className="size-3" />
                            {assignment.workspace_name}
                          </>
                        ) : (
                          <>
                            <GlobeIcon className="size-3 text-blue-500" />
                            Organization-wide
                          </>
                        )}
                      </span>
                    </div>
                    {canDeleteAssignment && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleRemoveRole(assignment.id)}
                        disabled={deleteUserAssignmentIsPending}
                        className="text-rose-500 hover:text-rose-600"
                      >
                        <Trash2Icon className="size-4" />
                      </Button>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex h-full items-center justify-center p-4">
                <p className="text-sm text-muted-foreground">
                  No role assignments
                </p>
              </div>
            )}
          </ScrollArea>
        </div>
      </div>
      {!canReadRbac && (
        <p className="text-sm text-muted-foreground">
          You do not have permission to manage RBAC assignments.
        </p>
      )}
      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChange(false)}>
          Done
        </Button>
      </DialogFooter>
      <AlertDialog
        open={pendingOrgRemovalId !== null}
        onOpenChange={(open) => {
          if (!open) setPendingOrgRemovalId(null)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Remove organization role?</AlertDialogTitle>
            <AlertDialogDescription>
              {member.email} will no longer be listed as an organization member
              unless a group grants them an organization role. Workspace roles
              are kept.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={deleteUserAssignmentIsPending}
              onClick={handleConfirmOrgRemoval}
            >
              Remove role
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </DialogContent>
  )
}

const defaultToolbarProps: DataTableToolbarProps<OrgMemberRead> = {
  filterProps: {
    placeholder: "Filter by email...",
    column: "email",
  },
}
