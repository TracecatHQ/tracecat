"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import {
  FolderIcon,
  GlobeIcon,
  MinusIcon,
  PlusIcon,
  SearchIcon,
  UserPlusIcon,
  UsersIcon,
} from "lucide-react"
import { useMemo, useState } from "react"
import type {
  GroupReadWithMembers,
  GroupRoleAssignmentReadWithDetails,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import {
  RbacDetailRow,
  RbacListContainer,
  RbacListEmpty,
  RbacListHeader,
  RbacListItem,
} from "@/components/organization/rbac-list-item"
import {
  Accordion,
  AccordionContent,
  AccordionItem,
  AccordionTrigger,
} from "@/components/ui/accordion"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import {
  useOrgMembers,
  useRbacAssignments,
  useRbacGroup,
  useRbacGroups,
  useRbacRoles,
  useWorkspaceManager,
} from "@/lib/hooks"
import { rolesForScope } from "@/lib/rbac"

type GroupSection = "members" | "roles"

const DEFAULT_OPEN_SECTIONS: GroupSection[] = ["members", "roles"]

interface GroupPermissions {
  canUpdateGroup: boolean
  canDeleteGroup: boolean
  canCreateAssignments: boolean
  canDeleteAssignments: boolean
}

function isGroupSection(value: string): value is GroupSection {
  return value === "members" || value === "roles"
}

/**
 * Organization groups list with inline member and role management.
 */
export function OrgRbacGroups() {
  const [selectedGroup, setSelectedGroup] =
    useState<GroupReadWithMembers | null>(null)
  const [expandedGroupId, setExpandedGroupId] = useState<string | null>(null)
  const [openSections, setOpenSections] = useState<GroupSection[]>(
    DEFAULT_OPEN_SECTIONS
  )
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [isEditOpen, setIsEditOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState("")
  const {
    groups,
    isLoading,
    error,
    createGroup,
    createGroupIsPending,
    updateGroup,
    updateGroupIsPending,
    deleteGroup,
    deleteGroupIsPending,
  } = useRbacGroups()
  const { assignments: allAssignments = [] } = useRbacAssignments()
  const canCreateGroup = useScopeCheck("org:rbac:create") === true
  const canUpdateGroup = useScopeCheck("org:rbac:update") === true
  const canDeleteGroup = useScopeCheck("org:rbac:delete") === true
  const permissions: GroupPermissions = {
    canUpdateGroup,
    canDeleteGroup,
    canCreateAssignments: canCreateGroup,
    canDeleteAssignments: canDeleteGroup,
  }

  const filteredGroups = useMemo(() => {
    if (!searchQuery.trim()) return groups
    const query = searchQuery.toLowerCase()
    return groups.filter(
      (group) =>
        group.name.toLowerCase().includes(query) ||
        group.description?.toLowerCase().includes(query)
    )
  }, [groups, searchQuery])

  const handleCreateGroup = async (name: string, description: string) => {
    await createGroup({ name, description: description || undefined })
    setIsCreateOpen(false)
  }

  const handleUpdateGroup = async (
    groupId: string,
    name: string,
    description: string
  ) => {
    await updateGroup({ groupId, name, description: description || undefined })
    setIsEditOpen(false)
    setSelectedGroup(null)
  }

  const handleDeleteGroup = async () => {
    if (selectedGroup) {
      await deleteGroup(selectedGroup.id)
      setSelectedGroup(null)
    }
  }

  function handleExpandedChange(groupId: string, expanded: boolean) {
    setExpandedGroupId(expanded ? groupId : null)
    if (expanded) {
      setOpenSections(DEFAULT_OPEN_SECTIONS)
    }
  }

  const assignmentsByGroupId = useMemo(() => {
    const map = new Map<string, GroupRoleAssignmentReadWithDetails[]>()
    for (const assignment of allAssignments) {
      const next = map.get(assignment.group_id) ?? []
      next.push(assignment)
      map.set(assignment.group_id, next)
    }
    return map
  }, [allAssignments])

  if (error) {
    return (
      <div className="flex items-center justify-center py-12 text-sm text-destructive">
        Failed to load groups
      </div>
    )
  }

  function renderGroups() {
    if (isLoading) {
      return [1, 2, 3].map((i) => (
        <div
          key={i}
          className="flex items-center gap-3 border-b border-border/50 px-3 py-2.5 last:border-b-0"
        >
          <Skeleton className="size-6" />
          <Skeleton className="size-4" />
          <div className="flex-1 space-y-1.5">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-3 w-48" />
          </div>
        </div>
      ))
    }
    if (filteredGroups.length === 0) {
      return (
        <RbacListEmpty
          message={
            searchQuery ? "No groups match your search" : "No groups found"
          }
        />
      )
    }
    return filteredGroups.map((group) => (
      <GroupListItem
        key={group.id}
        group={group}
        assignments={assignmentsByGroupId.get(group.id) ?? []}
        isExpanded={expandedGroupId === group.id}
        onExpandedChange={(expanded) =>
          handleExpandedChange(group.id, expanded)
        }
        openSections={openSections}
        onOpenSectionsChange={setOpenSections}
        onEdit={() => {
          setSelectedGroup(group)
          setIsEditOpen(true)
        }}
        onDelete={() => setSelectedGroup(group)}
        permissions={permissions}
      />
    ))
  }

  return (
    <Dialog
      open={isCreateOpen || isEditOpen}
      onOpenChange={(open) => {
        if (!open) {
          setIsCreateOpen(false)
          setIsEditOpen(false)
          setSelectedGroup(null)
        }
      }}
    >
      <AlertDialog
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setSelectedGroup(null)
          }
        }}
      >
        <div className="space-y-4">
          <RbacListHeader
            left={
              <div className="relative">
                <SearchIcon className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Search groups..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="h-9 w-[250px] pl-8"
                />
              </div>
            }
            right={
              canCreateGroup ? (
                <DialogTrigger asChild>
                  <Button size="sm" onClick={() => setIsCreateOpen(true)}>
                    <PlusIcon className="mr-2 size-4" />
                    Create group
                  </Button>
                </DialogTrigger>
              ) : null
            }
          />

          <RbacListContainer>{renderGroups()}</RbacListContainer>
        </div>

        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete group</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to delete the group{" "}
              <span className="font-semibold">{selectedGroup?.name}</span>? This
              action cannot be undone. All role assignments for this group will
              be removed.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={handleDeleteGroup}
              disabled={deleteGroupIsPending}
            >
              {deleteGroupIsPending ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {isCreateOpen && canCreateGroup && (
        <GroupFormDialog
          title="Create group"
          description="Create a new group to organize users and assign roles."
          onSubmit={handleCreateGroup}
          isPending={createGroupIsPending}
          onOpenChange={(open) => {
            if (!open) setIsCreateOpen(false)
          }}
        />
      )}

      {isEditOpen && selectedGroup && canUpdateGroup && (
        <GroupFormDialog
          title="Edit group"
          description="Update the group's name and description."
          initialData={selectedGroup}
          onSubmit={(name, description) =>
            handleUpdateGroup(selectedGroup.id, name, description)
          }
          isPending={updateGroupIsPending}
          onOpenChange={(open) => {
            if (!open) {
              setIsEditOpen(false)
              setSelectedGroup(null)
            }
          }}
        />
      )}
    </Dialog>
  )
}

function GroupListItem({
  group,
  assignments,
  isExpanded,
  onExpandedChange,
  openSections,
  onOpenSectionsChange,
  onEdit,
  onDelete,
  permissions,
}: {
  group: GroupReadWithMembers
  assignments: GroupRoleAssignmentReadWithDetails[]
  isExpanded: boolean
  onExpandedChange: (expanded: boolean) => void
  openSections: GroupSection[]
  onOpenSectionsChange: (sections: GroupSection[]) => void
  onEdit: () => void
  onDelete: () => void
  permissions: GroupPermissions
}) {
  const canUpdateMembers = permissions.canUpdateGroup && !group.is_idp_managed
  const hasGatedItems = permissions.canUpdateGroup || permissions.canDeleteGroup

  return (
    <RbacListItem
      icon={<UsersIcon className="size-4" />}
      title={group.name}
      badges={
        <>
          <Badge variant="secondary" className="text-[10px]">
            {group.member_count} member
            {group.member_count !== 1 && "s"}
          </Badge>
          {assignments.length > 0 && (
            <Badge variant="outline" className="text-[10px]">
              {assignments.length} role{assignments.length !== 1 && "s"}
            </Badge>
          )}
        </>
      }
      isExpanded={isExpanded}
      onExpandedChange={onExpandedChange}
      actions={
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              className="size-8 p-0 opacity-0 transition-opacity group-hover:opacity-100 data-[state=open]:opacity-100"
            >
              <span className="sr-only">Open menu</span>
              <DotsHorizontalIcon className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              onClick={() => navigator.clipboard.writeText(group.id)}
            >
              Copy group ID
            </DropdownMenuItem>
            {hasGatedItems && <DropdownMenuSeparator />}
            {permissions.canUpdateGroup && (
              <DialogTrigger asChild>
                <DropdownMenuItem onClick={onEdit}>Edit group</DropdownMenuItem>
              </DialogTrigger>
            )}
            {permissions.canDeleteGroup && (
              <AlertDialogTrigger asChild>
                <DropdownMenuItem
                  className="text-rose-500 focus:text-rose-600"
                  onClick={onDelete}
                >
                  Delete group
                </DropdownMenuItem>
              </AlertDialogTrigger>
            )}
          </DropdownMenuContent>
        </DropdownMenu>
      }
    >
      <GroupExpandedContent
        group={group}
        assignments={assignments}
        openSections={openSections}
        onOpenSectionsChange={onOpenSectionsChange}
        canUpdateMembers={canUpdateMembers}
        canCreateAssignments={permissions.canCreateAssignments}
        canDeleteAssignments={permissions.canDeleteAssignments}
      />
    </RbacListItem>
  )
}

function GroupExpandedContent({
  group,
  assignments,
  openSections,
  onOpenSectionsChange,
  canUpdateMembers,
  canCreateAssignments,
  canDeleteAssignments,
}: {
  group: GroupReadWithMembers
  assignments: GroupRoleAssignmentReadWithDetails[]
  openSections: GroupSection[]
  onOpenSectionsChange: (sections: GroupSection[]) => void
  canUpdateMembers: boolean
  canCreateAssignments: boolean
  canDeleteAssignments: boolean
}) {
  return (
    <div className="space-y-2">
      {group.description && (
        <RbacDetailRow label="Description">{group.description}</RbacDetailRow>
      )}
      <Accordion
        type="multiple"
        value={openSections}
        onValueChange={(sections) =>
          onOpenSectionsChange(sections.filter(isGroupSection))
        }
      >
        <AccordionItem value="members" className="border-border/50">
          <AccordionTrigger className="py-1.5 text-xs hover:no-underline">
            Members ({group.member_count ?? 0})
          </AccordionTrigger>
          <AccordionContent className="pb-2">
            <GroupMembersSection
              groupId={group.id}
              isIdpManaged={group.is_idp_managed === true}
              canUpdateMembers={canUpdateMembers}
            />
          </AccordionContent>
        </AccordionItem>
        <AccordionItem value="roles" className="border-b-0">
          <AccordionTrigger className="py-1.5 text-xs hover:no-underline">
            Roles ({assignments.length})
          </AccordionTrigger>
          <AccordionContent className="pb-0">
            <GroupRolesSection
              groupId={group.id}
              assignments={assignments}
              canCreateAssignments={canCreateAssignments}
              canDeleteAssignments={canDeleteAssignments}
            />
          </AccordionContent>
        </AccordionItem>
      </Accordion>
    </div>
  )
}

function GroupMembersSection({
  groupId,
  isIdpManaged,
  canUpdateMembers,
}: {
  groupId: string
  isIdpManaged: boolean
  canUpdateMembers: boolean
}) {
  const { group, isLoading, error } = useRbacGroup(groupId)
  const {
    addGroupMember,
    addGroupMemberIsPending,
    removeGroupMember,
    removeGroupMemberIsPending,
  } = useRbacGroups()
  const members = group?.members ?? []

  async function handleRemoveMember(userId: string) {
    try {
      await removeGroupMember({ groupId, userId })
    } catch (error) {
      console.error("Failed to remove group member", error)
    }
  }

  function renderMembers() {
    if (isLoading) {
      return <Skeleton className="h-4 w-48" />
    }
    if (error) {
      return <p className="text-xs text-destructive">Failed to load members</p>
    }
    if (members.length === 0) {
      return <p className="text-xs text-muted-foreground">No members yet</p>
    }
    return (
      <div className="max-h-64 overflow-y-auto">
        {members.map((member) => (
          <div
            key={member.user_id}
            className="flex items-center justify-between gap-2 py-1 text-xs"
          >
            <div className="flex min-w-0 items-center gap-2">
              <span className="truncate font-medium">{member.email}</span>
              {(member.first_name || member.last_name) && (
                <span className="truncate text-muted-foreground">
                  {[member.first_name, member.last_name]
                    .filter(Boolean)
                    .join(" ")}
                </span>
              )}
            </div>
            {canUpdateMembers && (
              <Button
                variant="ghost"
                size="sm"
                aria-label="Remove member"
                onClick={() => handleRemoveMember(member.user_id)}
                disabled={removeGroupMemberIsPending}
                className="size-6 p-0 text-muted-foreground hover:text-foreground"
              >
                <MinusIcon className="size-3.5" />
              </Button>
            )}
          </div>
        ))}
      </div>
    )
  }

  return (
    <div className="space-y-2">
      {isIdpManaged && (
        <p className="text-xs text-muted-foreground">
          Membership is managed by your identity provider. Edit members there.
        </p>
      )}
      {canUpdateMembers && (
        <GroupAddMemberControl
          existingMemberIds={members.map((member) => member.user_id)}
          onAdd={(userId) => addGroupMember({ groupId, userId })}
          isPending={addGroupMemberIsPending}
        />
      )}
      {renderMembers()}
    </div>
  )
}

function GroupAddMemberControl({
  existingMemberIds,
  onAdd,
  isPending,
}: {
  existingMemberIds: string[]
  onAdd: (userId: string) => Promise<unknown>
  isPending: boolean
}) {
  const [selectedUserId, setSelectedUserId] = useState("")
  const { orgMembers } = useOrgMembers()

  // Filter out users who are already members
  const existing = new Set(existingMemberIds)
  const availableMembers = (orgMembers ?? []).flatMap((member) => {
    const userId = member.user_id
    if (!userId || existing.has(userId)) {
      return []
    }
    return [{ ...member, user_id: userId }]
  })

  async function handleAddMember() {
    if (!selectedUserId) return
    try {
      await onAdd(selectedUserId)
      setSelectedUserId("")
    } catch (error) {
      console.error("Failed to add group member", error)
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Select value={selectedUserId} onValueChange={setSelectedUserId}>
        <SelectTrigger className="h-8 flex-1 text-xs" aria-label="User">
          <SelectValue placeholder="Select a user" />
        </SelectTrigger>
        <SelectContent>
          {availableMembers.length === 0 ? (
            <p className="p-2 text-center text-sm text-muted-foreground">
              No available users
            </p>
          ) : (
            availableMembers.map((member) => (
              <SelectItem key={member.user_id} value={member.user_id}>
                {member.email}
                {member.first_name && ` (${member.first_name})`}
              </SelectItem>
            ))
          )}
        </SelectContent>
      </Select>
      <Button
        type="button"
        variant="ghost"
        size="sm"
        aria-label="Add member"
        className="size-6 p-0"
        onClick={handleAddMember}
        disabled={!selectedUserId || isPending}
      >
        <UserPlusIcon className="size-3.5" />
      </Button>
    </div>
  )
}

function GroupRolesSection({
  groupId,
  assignments,
  canCreateAssignments,
  canDeleteAssignments,
}: {
  groupId: string
  assignments: GroupRoleAssignmentReadWithDetails[]
  canCreateAssignments: boolean
  canDeleteAssignments: boolean
}) {
  const {
    createAssignment,
    createAssignmentIsPending,
    deleteAssignment,
    deleteAssignmentIsPending,
  } = useRbacAssignments()

  async function handleRemoveRole(assignmentId: string) {
    try {
      await deleteAssignment(assignmentId)
    } catch (error) {
      console.error("Failed to remove role assignment", error)
    }
  }

  return (
    <div className="space-y-2">
      {canCreateAssignments && (
        <GroupAddRoleControl
          onAdd={(roleId, workspaceId) =>
            createAssignment({
              group_id: groupId,
              role_id: roleId,
              workspace_id: workspaceId,
            })
          }
          isPending={createAssignmentIsPending}
        />
      )}
      {assignments.length === 0 ? (
        <p className="text-xs text-muted-foreground">No roles</p>
      ) : (
        <div className="max-h-64 overflow-y-auto">
          {assignments.map((assignment) => (
            <div
              key={assignment.id}
              className="flex items-center justify-between gap-2 py-1 text-xs"
            >
              <div className="flex min-w-0 items-center gap-2">
                <Badge variant="secondary">{assignment.role_name}</Badge>
                {assignment.workspace_name ? (
                  <span className="flex items-center gap-1 text-muted-foreground">
                    <FolderIcon className="size-3" />
                    {assignment.workspace_name}
                  </span>
                ) : (
                  <span className="flex items-center gap-1 text-blue-600 dark:text-blue-400">
                    <GlobeIcon className="size-3" />
                    Organization-wide
                  </span>
                )}
              </div>
              {canDeleteAssignments && (
                <Button
                  variant="ghost"
                  size="sm"
                  aria-label="Remove role"
                  onClick={() => handleRemoveRole(assignment.id)}
                  disabled={deleteAssignmentIsPending}
                  className="size-6 p-0 text-muted-foreground hover:text-foreground"
                >
                  <MinusIcon className="size-3.5" />
                </Button>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function GroupAddRoleControl({
  onAdd,
  isPending,
}: {
  onAdd: (roleId: string, workspaceId: string | null) => Promise<unknown>
  isPending: boolean
}) {
  const [selectedRoleId, setSelectedRoleId] = useState("")
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState("org-wide")
  const { roles } = useRbacRoles()
  const { workspaces } = useWorkspaceManager()
  const workspaceId =
    selectedWorkspaceId === "org-wide" ? null : selectedWorkspaceId

  async function handleAddRole() {
    if (!selectedRoleId) return
    try {
      await onAdd(selectedRoleId, workspaceId)
      setSelectedRoleId("")
      setSelectedWorkspaceId("org-wide")
    } catch (error) {
      console.error("Failed to add role assignment", error)
    }
  }

  return (
    <div className="flex items-center gap-2">
      <Select value={selectedRoleId} onValueChange={setSelectedRoleId}>
        <SelectTrigger className="h-8 flex-1 text-xs" aria-label="Role">
          <SelectValue placeholder="Select a role" />
        </SelectTrigger>
        <SelectContent>
          {rolesForScope(roles, workspaceId).map((role) => (
            <SelectItem key={role.id} value={role.id}>
              {role.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <Select
        value={selectedWorkspaceId}
        onValueChange={(value) => {
          setSelectedWorkspaceId(value)
          setSelectedRoleId("")
        }}
      >
        <SelectTrigger className="h-8 w-[180px] text-xs" aria-label="Scope">
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
        variant="ghost"
        size="sm"
        aria-label="Add role"
        className="size-6 p-0"
        onClick={handleAddRole}
        disabled={!selectedRoleId || isPending}
      >
        <PlusIcon className="size-3.5" />
      </Button>
    </div>
  )
}

function getSubmitLabel(isEdit: boolean, isPending: boolean): string {
  if (isPending) {
    return isEdit ? "Saving..." : "Creating..."
  }
  return isEdit ? "Save changes" : "Create group"
}

function GroupFormDialog({
  title,
  description,
  initialData,
  onSubmit,
  isPending,
  onOpenChange,
}: {
  title: string
  description: string
  initialData?: GroupReadWithMembers
  onSubmit: (name: string, description: string) => Promise<void>
  isPending: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [name, setName] = useState(initialData?.name ?? "")
  const [groupDescription, setGroupDescription] = useState(
    initialData?.description ?? ""
  )

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim()) return
    await onSubmit(name.trim(), groupDescription.trim())
  }

  return (
    <DialogContent>
      <form onSubmit={handleSubmit}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label htmlFor="group-name">Group name</Label>
            <Input
              id="group-name"
              placeholder="e.g., Security Team"
              value={name}
              onChange={(e) => setName(e.target.value)}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="group-description">Description (optional)</Label>
            <Textarea
              id="group-description"
              placeholder="Describe the purpose of this group"
              value={groupDescription}
              onChange={(e) => setGroupDescription(e.target.value)}
              rows={3}
            />
          </div>
        </div>
        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button type="submit" disabled={!name.trim() || isPending}>
            {getSubmitLabel(Boolean(initialData), isPending)}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  )
}
