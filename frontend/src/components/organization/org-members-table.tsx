"use client"

import { DialogTrigger } from "@radix-ui/react-dialog"
import { DotsHorizontalIcon, PlusIcon } from "@radix-ui/react-icons"
import { FolderIcon, GlobeIcon, Trash2Icon } from "lucide-react"
import { useSearchParams } from "next/navigation"
import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import {
  invitationsGetInvitationToken,
  type OrgMemberRead,
  type UserRoleAssignmentReadWithDetails,
} from "@/client"
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
import type { TracecatApiError } from "@/lib/errors"
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
        {/* Unmount on close so a reopen never shows another member's edits. */}
        {isChangeRoleOpen && selectedMember && (
          <ManageUserRolesDialog
            key={selectedMember.user_id}
            member={selectedMember}
            onOpenChange={setIsChangeRoleOpen}
          />
        )}
      </Dialog>
    </div>
  )
}

/**
 * A role assignment staged in the manage-roles dialog. `assignmentId` is set
 * for entries that already exist on the server.
 */
type PendingRoleAssignment = {
  key: string
  assignmentId: string | null
  roleId: string
  roleName: string
  workspaceId: string | null
  workspaceName: string | null
}

const ORG_WIDE_SCOPE = "org-wide"

// organization-member is the baseline every member holds; never shown or removed here.
const BASELINE_ROLE_SLUG = "organization-member"

function scopeKey(workspaceId: string | null): string {
  return workspaceId ?? ORG_WIDE_SCOPE
}

/** True when both lists hold the same assignment IDs with the same roles. */
function sameAssignments(
  a: UserRoleAssignmentReadWithDetails[],
  b: UserRoleAssignmentReadWithDetails[]
): boolean {
  if (a.length !== b.length) return false
  const roleById = new Map(a.map((x) => [x.id, x.role_id]))
  return b.every((x) => roleById.get(x.id) === x.role_id)
}

/**
 * Stage and apply a member's role assignments, then apply them on Done.
 *
 * Emptying the visible assignments removes the member from the organization.
 */
export function ManageUserRolesDialog({
  member,
  onOpenChange,
}: {
  member: OrgMemberRead
  onOpenChange: (open: boolean) => void
}) {
  const [roleId, setRoleId] = useState("")
  const [workspaceId, setWorkspaceId] = useState<string>(ORG_WIDE_SCOPE)
  const [pending, setPending] = useState<PendingRoleAssignment[]>([])
  // Server state captured at seed time; edits diff against this, never live data.
  const [baseline, setBaseline] = useState<UserRoleAssignmentReadWithDetails[]>(
    []
  )
  const [isConfirmOpen, setIsConfirmOpen] = useState(false)
  const [isApplying, setIsApplying] = useState(false)
  const seededRef = useRef(false)
  const userId = member.user_id ?? undefined

  const {
    userAssignments,
    isLoading: userAssignmentsIsLoading,
    refetchUserAssignments,
    createUserAssignment,
    updateUserAssignment,
    deleteUserAssignment,
  } = useRbacUserAssignments({ userId })
  const { deleteOrgMember } = useOrgMembers()
  const { roles, isLoading: rolesIsLoading } = useRbacRoles()
  const { workspaces } = useWorkspaceManager()
  const baselineRoleIds = useMemo(
    () =>
      new Set(
        roles
          .filter((role) => role.slug === BASELINE_ROLE_SLUG)
          .map((role) => role.id)
      ),
    [roles]
  )
  // The baseline role is invisible here: it is never listed, staged, or deleted.
  const visible = useCallback(
    (assignments: UserRoleAssignmentReadWithDetails[]) =>
      assignments.filter((a) => !baselineRoleIds.has(a.role_id)),
    [baselineRoleIds]
  )
  const canReadRbac = useScopeCheck("org:rbac:read") === true
  const canCreateAssignment = useScopeCheck("org:rbac:create") === true
  const canRemoveMember = useScopeCheck("org:member:remove") === true
  const canUpdateAssignment = useScopeCheck("org:rbac:update") === true
  const canDeleteAssignment = useScopeCheck("org:rbac:delete") === true

  const toPending = useCallback(
    (assignment: UserRoleAssignmentReadWithDetails): PendingRoleAssignment => ({
      key: scopeKey(assignment.workspace_id ?? null),
      assignmentId: assignment.id,
      roleId: assignment.role_id,
      roleName: assignment.role_name,
      workspaceId: assignment.workspace_id ?? null,
      workspaceName: assignment.workspace_name ?? null,
    }),
    []
  )

  const seed = useCallback(() => {
    const shown = visible(userAssignments)
    setBaseline(shown)
    setPending(shown.map(toPending))
  }, [userAssignments, toPending, visible])

  // Seed once per open: reseeding on every refetch would discard staged edits.
  useEffect(() => {
    if (seededRef.current || userAssignmentsIsLoading || rolesIsLoading) return
    seededRef.current = true
    seed()
  }, [userAssignmentsIsLoading, rolesIsLoading, seed])

  // Closing discards staged edits so the next open reseeds from server state.
  const closeDialog = useCallback(() => {
    setPending([])
    setBaseline([])
    setRoleId("")
    setWorkspaceId(ORG_WIDE_SCOPE)
    seededRef.current = false
    onOpenChange(false)
  }, [onOpenChange])

  const persistedByKey = useMemo(() => {
    const map = new Map<string, UserRoleAssignmentReadWithDetails>()
    for (const assignment of baseline) {
      map.set(scopeKey(assignment.workspace_id ?? null), assignment)
    }
    return map
  }, [baseline])

  const handleAddRole = () => {
    const role = roles.find((r) => r.id === roleId)
    if (!role) return
    const targetWorkspaceId =
      workspaceId === ORG_WIDE_SCOPE ? null : workspaceId
    const workspace = workspaces?.find((w) => w.id === targetWorkspaceId)
    const key = scopeKey(targetWorkspaceId)
    // One assignment per scope on the backend, so a same-scope add replaces.
    // Fall back to the persisted baseline: re-adding a scope removed earlier in
    // this session must reuse its ID, or Done would create a duplicate and 409.
    const existing = pending.find((entry) => entry.key === key)
    const persistedId = persistedByKey.get(key)?.id ?? null
    const next: PendingRoleAssignment = {
      key,
      assignmentId: existing?.assignmentId ?? persistedId,
      roleId: role.id,
      roleName: role.name,
      workspaceId: targetWorkspaceId,
      workspaceName: workspace?.name ?? null,
    }
    setPending((current) => [
      ...current.filter((entry) => entry.key !== key),
      next,
    ])
    setRoleId("")
    setWorkspaceId(ORG_WIDE_SCOPE)
  }

  const handleRemoveRole = (key: string) => {
    setPending((current) => current.filter((entry) => entry.key !== key))
  }

  const updates = useMemo(
    () =>
      pending.filter((entry) => {
        const persisted = persistedByKey.get(entry.key)
        return (
          entry.assignmentId !== null &&
          persisted != null &&
          persisted.role_id !== entry.roleId
        )
      }),
    [pending, persistedByKey]
  )
  const creates = useMemo(
    () => pending.filter((entry) => entry.assignmentId === null),
    [pending]
  )
  const deletes = useMemo(
    () =>
      baseline.filter(
        (assignment) =>
          !pending.some(
            (entry) =>
              entry.key === scopeKey(assignment.workspace_id ?? null) &&
              entry.assignmentId === assignment.id
          )
      ),
    [baseline, pending]
  )

  const isRemoval = pending.length === 0 && baseline.length > 0
  const needsCreate = creates.length > 0
  const needsUpdate = updates.length > 0
  const needsDelete = deletes.length > 0
  const hasChanges = needsCreate || needsUpdate || needsDelete

  const consequences = useMemo(() => {
    const messages: string[] = []
    if (isRemoval) {
      messages.push(
        `${member.email} will be removed from the organization: tokens revoked and group memberships dropped.`
      )
      return messages
    }
    const hadOrgWide = persistedByKey.has(ORG_WIDE_SCOPE)
    const hasOrgWide = pending.some((entry) => entry.workspaceId === null)
    if (hadOrgWide && !hasOrgWide) {
      messages.push(
        "No organization role. Presence is kept while workspace roles remain."
      )
    }
    for (const assignment of deletes) {
      if (assignment.workspace_id) {
        messages.push(
          `Loses access to workspace ${assignment.workspace_name ?? assignment.workspace_id}.`
        )
      }
    }
    return messages
  }, [isRemoval, member.email, persistedByKey, pending, deletes])

  const missingPermission =
    (isRemoval && !canRemoveMember) ||
    (needsCreate && !canCreateAssignment) ||
    (needsUpdate && !canUpdateAssignment) ||
    (needsDelete && !canDeleteAssignment)

  const applyChanges = async () => {
    if (!userId) return
    setIsApplying(true)
    // Another admin may have changed roles since seed; never apply a stale diff.
    const { data: live } = await refetchUserAssignments()
    const liveVisible = visible(live ?? [])
    if (!sameAssignments(baseline, liveVisible)) {
      toast({
        title: "Roles changed elsewhere",
        description: "Reloaded the current roles. Review and apply again.",
      })
      setBaseline(liveVisible)
      setPending(liveVisible.map(toPending))
      setIsApplying(false)
      setIsConfirmOpen(false)
      return
    }
    try {
      if (isRemoval) {
        await deleteOrgMember({ userId })
      } else {
        for (const entry of updates) {
          if (!entry.assignmentId) continue
          await updateUserAssignment({
            assignmentId: entry.assignmentId,
            role_id: entry.roleId,
          })
        }
        // Creates precede deletes: deleting the user's last path first would
        // drop them from the organization and 404 the create.
        for (const entry of creates) {
          await createUserAssignment({
            user_id: userId,
            role_id: entry.roleId,
            workspace_id: entry.workspaceId,
          })
        }
        for (const assignment of deletes) {
          await deleteUserAssignment(assignment.id)
        }
      }
    } catch (error) {
      const detail = (error as TracecatApiError).body?.detail
      toast({
        title: "Could not apply role changes",
        description: String(detail ?? "The request could not be completed."),
        variant: "destructive",
      })
      const { data } = await refetchUserAssignments()
      const refreshed = visible(data ?? [])
      setBaseline(refreshed)
      setPending(refreshed.map(toPending))
      setIsApplying(false)
      setIsConfirmOpen(false)
      return
    }
    await refetchUserAssignments()
    setIsApplying(false)
    setIsConfirmOpen(false)
    closeDialog()
  }

  const handleDone = () => {
    if (consequences.length > 0) {
      setIsConfirmOpen(true)
      return
    }
    void applyChanges()
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
                  {roles
                    .filter((role) => !baselineRoleIds.has(role.id))
                    .map((role) => (
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
                  <SelectItem value={ORG_WIDE_SCOPE}>
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
                disabled={!roleId || isApplying}
              >
                <PlusIcon className="size-4" />
              </Button>
            </div>
          </div>
        )}

        <div className="space-y-2">
          <Label>Current role assignments ({pending.length})</Label>
          <ScrollArea className="h-[200px] rounded-md border">
            {pending.length > 0 ? (
              <div className="space-y-2 p-4">
                {pending.map((entry) => (
                  <div
                    key={entry.key}
                    className="flex items-center justify-between rounded-md border p-2"
                  >
                    <div className="flex flex-col gap-1">
                      <div className="flex items-center gap-2">
                        <Badge variant="secondary">{entry.roleName}</Badge>
                      </div>
                      <span className="flex items-center gap-1 text-xs text-muted-foreground">
                        {entry.workspaceId ? (
                          <>
                            <FolderIcon className="size-3" />
                            {entry.workspaceName ?? entry.workspaceId}
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
                        onClick={() => handleRemoveRole(entry.key)}
                        disabled={isApplying}
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
        <Button variant="outline" onClick={closeDialog}>
          Cancel
        </Button>
        <Button
          onClick={handleDone}
          disabled={!hasChanges || isApplying || missingPermission}
        >
          Done
        </Button>
      </DialogFooter>
      <AlertDialog
        open={isConfirmOpen}
        onOpenChange={(open) => {
          if (!open) setIsConfirmOpen(false)
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Apply role changes?</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <ul className="list-disc space-y-1 pl-4">
                {consequences.map((consequence) => (
                  <li key={consequence}>{consequence}</li>
                ))}
              </ul>
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant={isRemoval ? "destructive" : "default"}
              disabled={isApplying}
              onClick={(event) => {
                event.preventDefault()
                void applyChanges()
              }}
            >
              Apply
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
