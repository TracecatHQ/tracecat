"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import {
  FolderIcon,
  GlobeIcon,
  PlusIcon,
  SearchIcon,
  ShieldIcon,
  Trash2Icon,
  UserMinusIcon,
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
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import {
  useOrgMembers,
  useRbacAssignments,
  useRbacGroups,
  useRbacRoles,
  useWorkspaceManager,
} from "@/lib/hooks"

export function OrgRbacGroups() {
  const [selectedGroup, setSelectedGroup] =
    useState<GroupReadWithMembers | null>(null)
  const [expandedGroupId, setExpandedGroupId] = useState<string | null>(null)
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [isEditOpen, setIsEditOpen] = useState(false)
  const [isManageOpen, setIsManageOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState("")
  const {
    groups,
    isLoading,
    error,
    getGroup,
    createGroup,
    createGroupIsPending,
    updateGroup,
    updateGroupIsPending,
    deleteGroup,
    deleteGroupIsPending,
    addGroupMember,
    addGroupMemberIsPending,
    removeGroupMember,
    removeGroupMemberIsPending,
  } = useRbacGroups()
  const { assignments: allAssignments = [] } = useRbacAssignments()
  const canCreateGroup = useScopeCheck("org:rbac:create") === true
  const canUpdateGroup = useScopeCheck("org:rbac:update") === true
  const canDeleteGroup = useScopeCheck("org:rbac:delete") === true

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

  const handleOpenManage = async (group: GroupReadWithMembers) => {
    try {
      // Fetch fresh group data with members
      const freshGroup = await getGroup(group.id)
      setSelectedGroup(freshGroup)
      setIsManageOpen(true)
    } catch (error) {
      console.error("Failed to load group details", error)
      setSelectedGroup(null)
      setIsManageOpen(false)
    }
  }

  const handleAddMember = async (userId: string) => {
    if (selectedGroup) {
      try {
        await addGroupMember({ groupId: selectedGroup.id, userId })
        // Refresh group data
        const freshGroup = await getGroup(selectedGroup.id)
        setSelectedGroup(freshGroup)
      } catch (error) {
        console.error("Failed to add group member", error)
        setSelectedGroup(null)
        setIsManageOpen(false)
      }
    }
  }

  const handleRemoveMember = async (userId: string) => {
    if (selectedGroup) {
      try {
        await removeGroupMember({ groupId: selectedGroup.id, userId })
        // Refresh group data
        const freshGroup = await getGroup(selectedGroup.id)
        setSelectedGroup(freshGroup)
      } catch (error) {
        console.error("Failed to remove group member", error)
        setSelectedGroup(null)
        setIsManageOpen(false)
      }
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

          {isLoading ? (
            <RbacListContainer>
              {[1, 2, 3].map((i) => (
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
              ))}
            </RbacListContainer>
          ) : filteredGroups.length === 0 ? (
            <RbacListContainer>
              <RbacListEmpty
                message={
                  searchQuery
                    ? "No groups match your search"
                    : "No groups found"
                }
              />
            </RbacListContainer>
          ) : (
            <RbacListContainer>
              {filteredGroups.map((group) => (
                <GroupListItem
                  key={group.id}
                  group={group}
                  assignments={assignmentsByGroupId.get(group.id) ?? []}
                  isExpanded={expandedGroupId === group.id}
                  onExpandedChange={(expanded) =>
                    setExpandedGroupId(expanded ? group.id : null)
                  }
                  onManage={() => handleOpenManage(group)}
                  onEdit={() => {
                    setSelectedGroup(group)
                    setIsEditOpen(true)
                  }}
                  onDelete={() => setSelectedGroup(group)}
                  canManageGroups={canUpdateGroup}
                  canDeleteGroups={canDeleteGroup}
                />
              ))}
            </RbacListContainer>
          )}
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

      <Dialog open={isManageOpen} onOpenChange={setIsManageOpen}>
        {selectedGroup && canUpdateGroup && (
          <GroupManageDialog
            group={selectedGroup}
            onAddMember={handleAddMember}
            onRemoveMember={handleRemoveMember}
            isAddingMember={addGroupMemberIsPending}
            isRemovingMember={removeGroupMemberIsPending}
            onOpenChange={setIsManageOpen}
            canManageMembers={canUpdateGroup}
            canCreateAssignments={canCreateGroup}
            canDeleteAssignments={canDeleteGroup}
          />
        )}
      </Dialog>
    </Dialog>
  )
}

function GroupListItem({
  group,
  assignments,
  isExpanded,
  onExpandedChange,
  onManage,
  onEdit,
  onDelete,
  canManageGroups,
  canDeleteGroups,
}: {
  group: GroupReadWithMembers
  assignments: GroupRoleAssignmentReadWithDetails[]
  isExpanded: boolean
  onExpandedChange: (expanded: boolean) => void
  onManage: () => void
  onEdit: () => void
  onDelete: () => void
  canManageGroups: boolean
  canDeleteGroups: boolean
}) {
  return (
    <RbacListItem
      icon={<UsersIcon className="size-4" />}
      title={group.name}
      subtitle={
        group.description ||
        `${group.member_count} member${group.member_count !== 1 ? "s" : ""}`
      }
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
            {(canManageGroups || canDeleteGroups) && <DropdownMenuSeparator />}
            {canManageGroups && (
              <>
                <DropdownMenuItem onClick={onManage}>
                  <UserPlusIcon className="mr-2 size-4" />
                  Manage group
                </DropdownMenuItem>
                <DialogTrigger asChild>
                  <DropdownMenuItem onClick={onEdit}>
                    Edit group
                  </DropdownMenuItem>
                </DialogTrigger>
              </>
            )}
            {canDeleteGroups && (
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
        onManage={onManage}
        canManageGroups={canManageGroups}
      />
    </RbacListItem>
  )
}

function GroupExpandedContent({
  group,
  assignments,
  onManage,
  canManageGroups,
}: {
  group: GroupReadWithMembers
  assignments: GroupRoleAssignmentReadWithDetails[]
  onManage: () => void
  canManageGroups: boolean
}) {
  return (
    <div className="space-y-3">
      {group.description && (
        <RbacDetailRow label="Description">{group.description}</RbacDetailRow>
      )}
      <RbacDetailRow label="Members">
        <div className="flex items-center gap-2">
          <span className="text-muted-foreground">
            {group.member_count} member
            {group.member_count !== 1 && "s"}
          </span>
          {canManageGroups && (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-xs"
              onClick={onManage}
            >
              <UserPlusIcon className="mr-1 size-3" />
              Manage
            </Button>
          )}
        </div>
      </RbacDetailRow>
      <RbacDetailRow label="Role assignments">
        {assignments.length === 0 ? (
          <span className="text-muted-foreground">No role assignments</span>
        ) : (
          <div className="space-y-1.5">
            {assignments.map((assignment) => (
              <div
                key={assignment.id}
                className="flex items-center gap-2 text-xs"
              >
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
            ))}
          </div>
        )}
      </RbacDetailRow>
    </div>
  )
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
            {isPending
              ? initialData
                ? "Saving..."
                : "Creating..."
              : initialData
                ? "Save changes"
                : "Create group"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  )
}

function GroupManageDialog({
  group,
  onAddMember,
  onRemoveMember,
  isAddingMember,
  isRemovingMember,
  onOpenChange,
  canManageMembers,
  canCreateAssignments,
  canDeleteAssignments,
}: {
  group: GroupReadWithMembers
  onAddMember: (userId: string) => Promise<void>
  onRemoveMember: (userId: string) => Promise<void>
  isAddingMember: boolean
  isRemovingMember: boolean
  onOpenChange: (open: boolean) => void
  canManageMembers: boolean
  canCreateAssignments: boolean
  canDeleteAssignments: boolean
}) {
  const [selectedUserId, setSelectedUserId] = useState<string>("")
  const [selectedRoleId, setSelectedRoleId] = useState<string>("")
  const [selectedWorkspaceId, setSelectedWorkspaceId] =
    useState<string>("org-wide")
  const { orgMembers } = useOrgMembers()
  const { roles } = useRbacRoles()
  const { workspaces } = useWorkspaceManager()
  const {
    assignments,
    createAssignment,
    createAssignmentIsPending,
    deleteAssignment,
    deleteAssignmentIsPending,
  } = useRbacAssignments({ groupId: group.id })

  // Filter out users who are already members
  const existingMemberIds = new Set(group.members?.map((m) => m.user_id) ?? [])
  const availableMembers = (orgMembers ?? []).flatMap((member) => {
    const userId = member.user_id
    if (!userId || existingMemberIds.has(userId)) {
      return []
    }
    return [{ ...member, user_id: userId }]
  })

  const handleAddMember = async () => {
    if (!canManageMembers || !selectedUserId) return
    await onAddMember(selectedUserId)
    setSelectedUserId("")
  }

  const handleAddRole = async () => {
    if (!canCreateAssignments || !selectedRoleId) return
    await createAssignment({
      group_id: group.id,
      role_id: selectedRoleId,
      workspace_id:
        selectedWorkspaceId === "org-wide" ? null : selectedWorkspaceId,
    })
    setSelectedRoleId("")
    setSelectedWorkspaceId("org-wide")
  }

  const handleRemoveRole = async (assignmentId: string) => {
    if (!canDeleteAssignments) return
    await deleteAssignment(assignmentId)
  }

  return (
    <DialogContent className="max-w-xl">
      <DialogHeader>
        <DialogTitle>Manage group - {group.name}</DialogTitle>
        <DialogDescription>
          Add or remove members and role assignments for this group.
        </DialogDescription>
      </DialogHeader>
      <Tabs defaultValue="members" className="w-full">
        <TabsList className="w-full">
          <TabsTrigger value="members" className="flex-1">
            <UsersIcon className="mr-2 size-4" />
            Members ({group.members?.length ?? 0})
          </TabsTrigger>
          <TabsTrigger value="roles" className="flex-1">
            <ShieldIcon className="mr-2 size-4" />
            Roles ({assignments.length})
          </TabsTrigger>
        </TabsList>
        <TabsContent value="members" className="mt-4 space-y-4">
          {canManageMembers && (
            <div className="space-y-2">
              <Label>Add member</Label>
              <div className="flex gap-2">
                <Select
                  value={selectedUserId}
                  onValueChange={setSelectedUserId}
                >
                  <SelectTrigger className="flex-1">
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
                  onClick={handleAddMember}
                  disabled={!selectedUserId || isAddingMember}
                >
                  <UserPlusIcon className="size-4" />
                </Button>
              </div>
            </div>
          )}

          <div className="space-y-2">
            <Label>Current members ({group.members?.length ?? 0})</Label>
            <ScrollArea className="h-[200px] rounded-md border">
              {group.members && group.members.length > 0 ? (
                <div className="space-y-2 p-4">
                  {group.members.map((member) => (
                    <div
                      key={member.user_id}
                      className="flex items-center justify-between rounded-md border p-2"
                    >
                      <div className="flex flex-col">
                        <span className="text-sm font-medium">
                          {member.email}
                        </span>
                        {(member.first_name || member.last_name) && (
                          <span className="text-xs text-muted-foreground">
                            {[member.first_name, member.last_name]
                              .filter(Boolean)
                              .join(" ")}
                          </span>
                        )}
                      </div>
                      {canManageMembers && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => onRemoveMember(member.user_id)}
                          disabled={isRemovingMember}
                          className="text-rose-500 hover:text-rose-600"
                        >
                          <UserMinusIcon className="size-4" />
                        </Button>
                      )}
                    </div>
                  ))}
                </div>
              ) : (
                <div className="flex h-full items-center justify-center p-4">
                  <p className="text-sm text-muted-foreground">
                    No members yet
                  </p>
                </div>
              )}
            </ScrollArea>
          </div>
        </TabsContent>

        <TabsContent value="roles" className="mt-4 space-y-4">
          {canCreateAssignments && (
            <div className="space-y-2">
              <Label>Add role assignment</Label>
              <div className="flex gap-2">
                <Select
                  value={selectedRoleId}
                  onValueChange={setSelectedRoleId}
                >
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
                <Select
                  value={selectedWorkspaceId}
                  onValueChange={setSelectedWorkspaceId}
                >
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
                  disabled={!selectedRoleId || createAssignmentIsPending}
                >
                  <PlusIcon className="size-4" />
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                All members of this group will inherit the assigned roles.
              </p>
            </div>
          )}

          <div className="space-y-2">
            <Label>Current role assignments ({assignments.length})</Label>
            <ScrollArea className="h-[200px] rounded-md border">
              {assignments.length > 0 ? (
                <div className="space-y-2 p-4">
                  {assignments.map((assignment) => (
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
                      {canDeleteAssignments && (
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => handleRemoveRole(assignment.id)}
                          disabled={deleteAssignmentIsPending}
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
        </TabsContent>
      </Tabs>
      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChange(false)}>
          Done
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}
