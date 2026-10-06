"use client"

import { DialogTrigger } from "@radix-ui/react-dialog"
import { DotsHorizontalIcon, PlusIcon } from "@radix-ui/react-icons"
import type { ColumnDef } from "@tanstack/react-table"
import { FolderIcon, GlobeIcon, MinusIcon } from "lucide-react"
import { useMemo, useRef, useState } from "react"
import {
  type GroupRoleAssignmentReadWithDetails,
  invitationsGetInvitationToken,
  type OrgMemberRead,
  rbacListAssignments,
  rbacReplaceUserAssignments,
  type UserRoleAssignmentReadWithDetails,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  DataTable,
  DataTableColumnHeader,
  type DataTableToolbarProps,
} from "@/components/data-table"
import { AbbreviatedBadgeList } from "@/components/organization/abbreviated-badge-list"
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
import { useEntitlements } from "@/hooks/use-entitlements"
import { getApiErrorDetail, type TracecatApiError } from "@/lib/errors"
import { getRelativeTime } from "@/lib/event-history"
import {
  useOrgMembers,
  useRbacRoles,
  useRbacUserAssignments,
  useWorkspaceManager,
} from "@/lib/hooks"
import { useQuery, useQueryClient } from "@/lib/query"
import {
  abbreviateName,
  abbreviateRoleName,
  organizationTier,
  rolesForScope,
} from "@/lib/rbac"
import { toast } from "../ui/use-toast"

export function OrgMembersTable() {
  const [selectedMember, setSelectedMember] = useState<OrgMemberRead | null>(
    null
  )
  const [isChangeRoleOpen, setIsChangeRoleOpen] = useState(false)
  const [isSavingRoles, setIsSavingRoles] = useState(false)
  const [isRemoveMemberOpen, setIsRemoveMemberOpen] = useState(false)
  const [removeConfirmationEmail, setRemoveConfirmationEmail] = useState("")
  const canInviteMembers = useScopeCheck("org:member:invite") === true
  const canRemoveMembers = useScopeCheck("org:member:remove") === true
  const canReadRbac = useScopeCheck("org:rbac:read") === true
  const {
    orgMembers,
    deleteOrgMember,
    revokeInvitation,
    resendInvitation,
    resendInvitationIsPending,
  } = useOrgMembers()
  const { hasEntitlement, hasEntitlementData } = useEntitlements()
  const canReadGroups =
    canReadRbac && hasEntitlementData && hasEntitlement("rbac_addons")
  const roleMenuTrigger = useRef<HTMLButtonElement | null>(null)

  const accessColumns = useMemo(
    () => [
      ...(canReadRbac ? [ROLES_COLUMN, WORKSPACES_COLUMN] : []),
      ...(canReadGroups ? [GROUPS_COLUMN] : []),
    ],
    [canReadRbac, canReadGroups]
  )
  const filterFields = useMemo(
    () =>
      accessColumns.map((column) => ({
        column: column.id,
        title: column.filterTitle,
        options: filterOptions(orgMembers ?? [], column.getItems),
      })),
    [accessColumns, orgMembers]
  )

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
    fields: filterFields,
    actions: <InviteMemberDialogButton />,
  }

  return (
    <div className="space-y-4">
      <Dialog
        open={isChangeRoleOpen}
        onOpenChange={(open) => {
          if (!isSavingRoles) setIsChangeRoleOpen(open)
        }}
      >
        <AlertDialog
          open={isRemoveMemberOpen}
          onOpenChange={(isOpen) => {
            setIsRemoveMemberOpen(isOpen)
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
                accessorFn: (member) => organizationTier(member.role_slug),
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Organization"
                  />
                ),
                cell: ({ row }) => (
                  <div className="text-xs">
                    {organizationTier(row.original.role_slug)}
                  </div>
                ),
                enableSorting: true,
                enableHiding: false,
              },
              ...accessColumns.map(accessColumnDef),
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
                        <Button
                          variant="ghost"
                          className="size-8 p-0"
                          onFocus={(event) => {
                            roleMenuTrigger.current = event.currentTarget
                          }}
                          onPointerDown={(event) => {
                            roleMenuTrigger.current = event.currentTarget
                          }}
                        >
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
                                <DropdownMenuItem
                                  disabled={resendInvitationIsPending}
                                  onSelect={async () => {
                                    if (!member.invitation_id) return
                                    try {
                                      await resendInvitation(
                                        member.invitation_id
                                      )
                                      toast({
                                        title: "Invitation email queued",
                                        description: member.email,
                                      })
                                    } catch (error) {
                                      const apiError = error as TracecatApiError
                                      if (apiError.status === 409) {
                                        toast({
                                          title:
                                            "You just sent an invitation email",
                                          description:
                                            "Please try again shortly.",
                                        })
                                        return
                                      }
                                      toast({
                                        title: "Failed to resend invitation",
                                        description:
                                          getApiErrorDetail(apiError) ??
                                          undefined,
                                        variant: "destructive",
                                      })
                                    }
                                  }}
                                >
                                  Resend invitation email
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
        {isChangeRoleOpen && selectedMember && (
          <ManageUserRolesDialog
            key={selectedMember.user_id ?? selectedMember.email}
            member={selectedMember}
            onOpenChange={setIsChangeRoleOpen}
            onSavingChange={setIsSavingRoles}
            onCloseAutoFocus={(event) => {
              event.preventDefault()
              roleMenuTrigger.current?.focus()
            }}
          />
        )}
      </Dialog>
    </div>
  )
}

type NamedItem = { id: string; name: string }

type AccessColumn = {
  id: string
  title: string
  filterTitle: string
  getItems: (member: OrgMemberRead) => NamedItem[]
  abbreviate: (name: string) => string
}

const ROLES_COLUMN: AccessColumn = {
  id: "roles",
  title: "Roles",
  filterTitle: "Role",
  getItems: (member) => member.roles ?? [],
  abbreviate: abbreviateRoleName,
}

const WORKSPACES_COLUMN: AccessColumn = {
  id: "workspaces",
  title: "Workspace",
  filterTitle: "Workspace",
  getItems: (member) => member.workspaces ?? [],
  abbreviate: abbreviateName,
}

const GROUPS_COLUMN: AccessColumn = {
  id: "groups",
  title: "Groups",
  filterTitle: "Group",
  getItems: (member) => member.groups ?? [],
  abbreviate: abbreviateName,
}

function accessColumnDef({
  id,
  title,
  getItems,
  abbreviate,
}: AccessColumn): ColumnDef<OrgMemberRead> {
  function itemIds(member: OrgMemberRead) {
    return getItems(member).map((item) => item.id)
  }
  return {
    id,
    accessorFn: itemIds,
    getUniqueValues: itemIds,
    filterFn: "arrIncludesSome",
    header: ({ column }) => (
      <DataTableColumnHeader
        className="text-xs"
        column={column}
        title={title}
      />
    ),
    cell: ({ row }) => (
      <AbbreviatedBadgeList
        items={getItems(row.original)}
        abbreviate={abbreviate}
      />
    ),
    enableSorting: false,
    enableHiding: false,
  }
}

function filterOptions(
  members: OrgMemberRead[],
  getItems: (member: OrgMemberRead) => NamedItem[]
) {
  const namesById = new Map(
    members.flatMap(getItems).map((item) => [item.id, item.name])
  )
  return [...namesById]
    .map(([value, label]) => ({ label, value }))
    .sort((a, b) => a.label.localeCompare(b.label))
}

type DraftAssignment = Pick<
  UserRoleAssignmentReadWithDetails,
  "role_id" | "role_name" | "workspace_id" | "workspace_name"
>

async function readGroupAssignments(userId: string) {
  return (await rbacListAssignments({ userId })).items
}

/** Stage direct role edits while showing the group paths that retain access. */
export function ManageUserRolesDialog({
  member,
  onOpenChange,
  onSavingChange,
  onCloseAutoFocus,
}: {
  member: OrgMemberRead
  onOpenChange: (open: boolean) => void
  onSavingChange: (saving: boolean) => void
  onCloseAutoFocus?: (event: Event) => void
}) {
  const [roleId, setRoleId] = useState("")
  const [workspaceId, setWorkspaceId] = useState("org-wide")
  const [draft, setDraft] = useState<{
    original: UserRoleAssignmentReadWithDetails[]
    assignments: DraftAssignment[]
    groups: GroupRoleAssignmentReadWithDetails[] | null
  } | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const [isRemovalConfirmationOpen, setIsRemovalConfirmationOpen] =
    useState(false)
  const [saveError, setSaveError] = useState("")
  const userId = member.user_id ?? undefined
  const queryClient = useQueryClient()
  const canReadRbac = useScopeCheck("org:rbac:read") === true
  const canCreateAssignment = useScopeCheck("org:rbac:create") === true
  const canUpdateAssignment = useScopeCheck("org:rbac:update") === true
  const canDeleteAssignment = useScopeCheck("org:rbac:delete") === true
  const {
    userAssignments,
    isLoading: userAssignmentsIsLoading,
    error: userAssignmentsError,
  } = useRbacUserAssignments({
    userId,
    enabled: canReadRbac && Boolean(userId),
  })
  const {
    roles,
    isLoading: rolesIsLoading,
    error: rolesError,
  } = useRbacRoles({ enabled: canReadRbac })
  const { workspaces } = useWorkspaceManager()
  const { hasEntitlement, hasEntitlementData } = useEntitlements()
  const canReadGroups = hasEntitlementData && hasEntitlement("rbac_addons")
  const groupQueryKey = ["rbac-groups", "user-assignments", userId]
  const {
    data: groupAssignments = [],
    isLoading: groupsIsLoading,
    error: groupsError,
    isSuccess: groupsLoaded,
  } = useQuery({
    queryKey: groupQueryKey,
    queryFn: () => readGroupAssignments(userId!),
    enabled: canReadRbac && Boolean(userId) && canReadGroups,
    meta: { suppressErrorToast: true },
  })
  const ready =
    canReadRbac &&
    Boolean(userId) &&
    !userAssignmentsIsLoading &&
    !userAssignmentsError &&
    !rolesIsLoading &&
    !rolesError
  const groupAccessKnown = canReadGroups && groupsLoaded && !groupsError
  const assignments = draft?.assignments ?? userAssignments
  const original = draft?.original ?? userAssignments
  const visibleAssignments = assignments
  const visibleGroupAssignments = groupAssignments
  const selectedWorkspaceId = workspaceId === "org-wide" ? null : workspaceId
  const selectedOriginal = original.find(
    (assignment) => (assignment.workspace_id ?? null) === selectedWorkspaceId
  )
  const canAdd = selectedOriginal ? canUpdateAssignment : canCreateAssignment
  const updates = assignments.flatMap((assignment) => {
    const previous = original.find(
      (item) =>
        (item.workspace_id ?? null) === (assignment.workspace_id ?? null)
    )
    return previous && previous.role_id !== assignment.role_id
      ? [{ ...assignment, id: previous.id }]
      : []
  })
  const creates = assignments.filter(
    (assignment) =>
      !original.some(
        (item) =>
          (item.workspace_id ?? null) === (assignment.workspace_id ?? null)
      )
  )
  const deletes = original.filter(
    (assignment) =>
      !assignments.some(
        (item) =>
          (item.workspace_id ?? null) === (assignment.workspace_id ?? null)
      )
  )
  const hasChanges = updates.length + creates.length + deletes.length > 0
  // An empty final set is a valid save: the user stays a member on the floor.
  const leavesNoRoles =
    hasChanges &&
    visibleAssignments.length === 0 &&
    groupAccessKnown &&
    visibleGroupAssignments.length === 0
  const canSave =
    ready &&
    !isSaving &&
    (!updates.length || canUpdateAssignment) &&
    (!creates.length || canCreateAssignment) &&
    (!deletes.length || canDeleteAssignment)
  const removedRoles = deletes.map(
    (assignment) =>
      `${assignment.role_name} in ${assignment.workspace_name ?? "the organization"}`
  )

  function stageAssignments(next: DraftAssignment[]) {
    const groups = groupAccessKnown ? groupAssignments : null
    setDraft({
      original,
      assignments: next,
      groups: draft ? draft.groups : groups,
    })
    setSaveError("")
  }

  function handleAddRole() {
    const role = roles.find((item) => item.id === roleId)
    if (!ready || isSaving || !role || !canAdd) return
    const assignment = {
      role_id: role.id,
      role_name: role.name,
      workspace_id: selectedWorkspaceId,
      workspace_name: workspaces?.find(
        (workspace) => workspace.id === selectedWorkspaceId
      )?.name,
    }
    stageAssignments([
      ...assignments.filter(
        (item) => (item.workspace_id ?? null) !== selectedWorkspaceId
      ),
      assignment,
    ])
    setRoleId("")
  }

  function handleRemoveRole(assignment: DraftAssignment) {
    const previous = original.find(
      (item) =>
        (item.workspace_id ?? null) === (assignment.workspace_id ?? null)
    )
    const next = assignments.filter((item) => item !== assignment)
    // Removing an unsaved promotion or replacement undoes it; it must not
    // delete the persisted source assignment hidden underneath the draft.
    if (previous && previous.role_id !== assignment.role_id) next.push(previous)
    stageAssignments(next)
  }

  async function handleDone(removalConfirmed = false) {
    if (!hasChanges) {
      onOpenChange(false)
      return
    }
    if (!canSave || !userId || !draft) return
    if (!removalConfirmed && removedRoles.length > 0) {
      setIsRemovalConfirmationOpen(true)
      return
    }
    setIsSaving(true)
    onSavingChange(true)
    setSaveError("")
    try {
      await rbacReplaceUserAssignments({
        requestBody: {
          user_id: userId,
          assignments: assignments.map(({ role_id, workspace_id }) => ({
            role_id,
            workspace_id: workspace_id ?? null,
          })),
          expected_assignments: original,
          expected_group_assignments:
            draft.groups ?? (groupAccessKnown ? groupAssignments : null),
        },
      })
      await Promise.all([
        ...["rbac-user-assignments", "user-scopes", "org-members"].map((key) =>
          queryClient.invalidateQueries({ queryKey: [key] })
        ),
        queryClient.invalidateQueries({
          queryKey: ["workspace"],
          predicate: (query) => query.queryKey[2] === "members",
        }),
      ])
      onOpenChange(false)
    } catch (error) {
      setDraft(null)
      await queryClient.invalidateQueries({
        queryKey: ["rbac-user-assignments", userId],
      })
      await queryClient.invalidateQueries({ queryKey: groupQueryKey })
      setSaveError(
        error &&
          typeof error === "object" &&
          "status" in error &&
          error.status === 409
          ? "Roles or group access changed while this dialog was open. Review the current roles and try again."
          : "Changes could not be confirmed. Review the current roles before trying again."
      )
    } finally {
      setIsSaving(false)
      onSavingChange(false)
    }
  }

  return (
    <DialogContent
      className="max-w-2xl"
      aria-describedby={undefined}
      onCloseAutoFocus={onCloseAutoFocus}
    >
      <DialogHeader>
        <DialogTitle>Manage roles - {member.email}</DialogTitle>
      </DialogHeader>
      <div className="space-y-4 py-4">
        {(canCreateAssignment || canUpdateAssignment) && (
          <div className="space-y-2">
            <Label>Add role assignment</Label>
            <div className="flex gap-2">
              <Select
                value={roleId}
                onValueChange={setRoleId}
                disabled={!ready || isSaving}
              >
                <SelectTrigger className="min-w-0 flex-1" aria-label="Role">
                  <SelectValue placeholder="Select a role" />
                </SelectTrigger>
                <SelectContent>
                  {rolesForScope(roles, selectedWorkspaceId).map((role) => (
                    <SelectItem key={role.id} value={role.id}>
                      {role.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Select
                value={workspaceId}
                onValueChange={(value) => {
                  setWorkspaceId(value)
                  setRoleId("")
                }}
                disabled={!ready || isSaving}
              >
                <SelectTrigger
                  className="w-[180px] min-w-0 shrink"
                  aria-label="Scope"
                >
                  <SelectValue placeholder="Scope" />
                </SelectTrigger>
                <SelectContent className="max-w-[calc(100vw-2rem)]">
                  <SelectItem value="org-wide">Organization</SelectItem>
                  {workspaces?.map((workspace) => (
                    <SelectItem
                      key={workspace.id}
                      value={workspace.id}
                      className="break-words whitespace-normal"
                    >
                      {workspace.name}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <Button
                type="button"
                aria-label="Add role"
                onClick={handleAddRole}
                disabled={!roleId || !ready || isSaving || !canAdd}
              >
                <PlusIcon className="size-4" />
              </Button>
            </div>
          </div>
        )}
        <div className="space-y-2">
          <Label>
            Direct roles ({ready ? visibleAssignments.length : "…"})
          </Label>
          <ScrollArea className="h-[320px] rounded-md border">
            <div className="px-4 py-2">
              {ready &&
                visibleAssignments.map((assignment) => (
                  <div
                    key={assignment.workspace_id ?? "org-wide"}
                    className="flex min-h-9 items-center justify-between gap-2 border-b py-1 last:border-0"
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <Badge variant="secondary">{assignment.role_name}</Badge>
                      <span className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
                        {assignment.workspace_id ? (
                          <FolderIcon className="size-3" />
                        ) : (
                          <GlobeIcon className="size-3" />
                        )}
                        {assignment.workspace_name ?? "Organization-wide"}
                      </span>
                    </div>
                    {(canDeleteAssignment ||
                      !original.some(
                        (item) =>
                          (item.workspace_id ?? null) ===
                            (assignment.workspace_id ?? null) &&
                          item.role_id === assignment.role_id
                      )) && (
                      <Button
                        variant="ghost"
                        size="sm"
                        aria-label={`Remove ${assignment.role_name} from ${assignment.workspace_name ?? "organization"}`}
                        onClick={() => handleRemoveRole(assignment)}
                        disabled={isSaving}
                        className="size-7 shrink-0 p-0 text-muted-foreground hover:text-foreground"
                      >
                        <MinusIcon className="size-4" />
                      </Button>
                    )}
                  </div>
                ))}
              {ready && visibleAssignments.length === 0 && (
                <p className="text-sm text-muted-foreground">No direct roles</p>
              )}
              {!ready && (
                <p role="status" className="text-sm text-muted-foreground">
                  {userAssignmentsError || rolesError || groupsError
                    ? "Unable to load roles and group access. Close and reopen to retry."
                    : "Loading roles and group access…"}
                </p>
              )}
            </div>
          </ScrollArea>
        </div>
        {ready && groupAccessKnown && visibleGroupAssignments.length > 0 && (
          <div className="space-y-2">
            <Label>Roles from groups</Label>
            <ScrollArea className="rounded-md border [&>[data-radix-scroll-area-viewport]]:max-h-[320px]">
              <div className="px-4 py-2">
                {visibleGroupAssignments.map((assignment) => (
                  <div
                    key={assignment.id}
                    className="flex min-h-9 items-center justify-between gap-2 border-b py-1 last:border-0"
                  >
                    <div className="flex min-w-0 items-center gap-2">
                      <Badge variant="secondary">{assignment.role_name}</Badge>
                      <span className="flex min-w-0 items-center gap-1 text-xs text-muted-foreground">
                        {assignment.workspace_id ? (
                          <FolderIcon className="size-3" />
                        ) : (
                          <GlobeIcon className="size-3" />
                        )}
                        {assignment.workspace_name ?? "Organization-wide"}
                      </span>
                    </div>
                    <span className="shrink-0 text-xs text-muted-foreground">
                      via {assignment.group_name}
                    </span>
                  </div>
                ))}
              </div>
            </ScrollArea>
            <p className="text-xs text-muted-foreground">
              Manage these roles through the group.
            </p>
          </div>
        )}
        {ready && !groupAccessKnown && !groupsIsLoading && (
          <p role="status" className="text-sm text-muted-foreground">
            Group access cannot be checked. Only direct roles are shown.
          </p>
        )}
        {saveError && (
          <p role="alert" className="text-sm text-destructive">
            {saveError}
          </p>
        )}
      </div>
      <DialogFooter>
        <Button
          variant="outline"
          disabled={isSaving}
          onClick={() => onOpenChange(false)}
        >
          Cancel
        </Button>
        <Button
          disabled={isSaving || (hasChanges && !canSave)}
          onClick={() => handleDone()}
        >
          {isSaving ? "Saving…" : "Done"}
        </Button>
      </DialogFooter>
      <AlertDialog
        open={isRemovalConfirmationOpen}
        onOpenChange={setIsRemovalConfirmationOpen}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Save role changes?</AlertDialogTitle>
            <AlertDialogDescription>
              This will remove {removedRoles.join(", ")} from {member.email}.
              {visibleGroupAssignments.length > 0 &&
                " Access from groups is unchanged."}
              {leavesNoRoles &&
                " This leaves the user with no roles. They stay a member with baseline access. Use Remove member to remove them."}
              {(updates.length > 0 || creates.length > 0) &&
                " Your other role changes will also be saved."}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              disabled={!canSave}
              onClick={() => handleDone(true)}
            >
              Confirm changes
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
