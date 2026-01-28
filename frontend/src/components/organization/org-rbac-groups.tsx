"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { PlusIcon, UserMinusIcon, UserPlusIcon, UsersIcon } from "lucide-react"
import { useState } from "react"
import type { GroupReadWithMembers } from "@/client"
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
import { Textarea } from "@/components/ui/textarea"
import { useOrgMembers, useRbacGroups } from "@/lib/hooks"

export function OrgRbacGroups() {
  const [selectedGroup, setSelectedGroup] =
    useState<GroupReadWithMembers | null>(null)
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [isEditOpen, setIsEditOpen] = useState(false)
  const [isMembersOpen, setIsMembersOpen] = useState(false)
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

  const handleOpenMembers = async (group: GroupReadWithMembers) => {
    // Fetch fresh group data with members
    const freshGroup = await getGroup(group.id)
    setSelectedGroup(freshGroup)
    setIsMembersOpen(true)
  }

  const handleAddMember = async (userId: string) => {
    if (selectedGroup) {
      await addGroupMember({ groupId: selectedGroup.id, userId })
      // Refresh group data
      const freshGroup = await getGroup(selectedGroup.id)
      setSelectedGroup(freshGroup)
    }
  }

  const handleRemoveMember = async (userId: string) => {
    if (selectedGroup) {
      await removeGroupMember({ groupId: selectedGroup.id, userId })
      // Refresh group data
      const freshGroup = await getGroup(selectedGroup.id)
      setSelectedGroup(freshGroup)
    }
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
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              Groups are collections of users that can be assigned roles.
            </p>
            <DialogTrigger asChild>
              <Button size="sm" onClick={() => setIsCreateOpen(true)}>
                <PlusIcon className="mr-2 size-4" />
                Create group
              </Button>
            </DialogTrigger>
          </div>

          <DataTable
            data={groups}
            isLoading={isLoading}
            error={error as Error | null}
            emptyMessage="No groups found"
            initialSortingState={[{ id: "name", desc: false }]}
            columns={[
              {
                accessorKey: "name",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Group"
                  />
                ),
                cell: ({ row }) => (
                  <div className="flex items-center gap-2">
                    <UsersIcon className="size-4 text-muted-foreground" />
                    <span className="text-sm font-medium">
                      {row.getValue<string>("name")}
                    </span>
                  </div>
                ),
                enableSorting: true,
                enableHiding: false,
              },
              {
                accessorKey: "description",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Description"
                  />
                ),
                cell: ({ row }) => (
                  <span className="text-xs text-muted-foreground line-clamp-1">
                    {row.getValue<string>("description") || "-"}
                  </span>
                ),
                enableSorting: false,
                enableHiding: true,
              },
              {
                accessorKey: "member_count",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Members"
                  />
                ),
                cell: ({ row }) => (
                  <Badge variant="secondary">
                    {row.getValue<number>("member_count")} member
                    {row.getValue<number>("member_count") !== 1 && "s"}
                  </Badge>
                ),
                enableSorting: true,
                enableHiding: true,
              },
              {
                id: "actions",
                enableHiding: false,
                cell: ({ row }) => {
                  const group = row.original

                  return (
                    <DropdownMenu>
                      <DropdownMenuTrigger asChild>
                        <Button variant="ghost" className="size-8 p-0">
                          <span className="sr-only">Open menu</span>
                          <DotsHorizontalIcon className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        <DropdownMenuItem
                          onClick={() =>
                            navigator.clipboard.writeText(group.id)
                          }
                        >
                          Copy group ID
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DropdownMenuItem
                          onClick={() => handleOpenMembers(group)}
                        >
                          <UserPlusIcon className="mr-2 size-4" />
                          Manage members
                        </DropdownMenuItem>
                        <DialogTrigger asChild>
                          <DropdownMenuItem
                            onClick={() => {
                              setSelectedGroup(group)
                              setIsEditOpen(true)
                            }}
                          >
                            Edit group
                          </DropdownMenuItem>
                        </DialogTrigger>
                        <AlertDialogTrigger asChild>
                          <DropdownMenuItem
                            className="text-rose-500 focus:text-rose-600"
                            onClick={() => setSelectedGroup(group)}
                          >
                            Delete group
                          </DropdownMenuItem>
                        </AlertDialogTrigger>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  )
                },
              },
            ]}
            toolbarProps={toolbarProps}
          />
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

      {isCreateOpen && (
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

      {isEditOpen && selectedGroup && (
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

      <Dialog open={isMembersOpen} onOpenChange={setIsMembersOpen}>
        {selectedGroup && (
          <GroupMembersDialog
            group={selectedGroup}
            onAddMember={handleAddMember}
            onRemoveMember={handleRemoveMember}
            isAddingMember={addGroupMemberIsPending}
            isRemovingMember={removeGroupMemberIsPending}
            onOpenChange={setIsMembersOpen}
          />
        )}
      </Dialog>
    </Dialog>
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

function GroupMembersDialog({
  group,
  onAddMember,
  onRemoveMember,
  isAddingMember,
  isRemovingMember,
  onOpenChange,
}: {
  group: GroupReadWithMembers
  onAddMember: (userId: string) => Promise<void>
  onRemoveMember: (userId: string) => Promise<void>
  isAddingMember: boolean
  isRemovingMember: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [selectedUserId, setSelectedUserId] = useState<string>("")
  const { orgMembers } = useOrgMembers()

  // Filter out users who are already members
  const existingMemberIds = new Set(group.members?.map((m) => m.user_id) ?? [])
  const availableMembers =
    orgMembers?.filter((m) => !existingMemberIds.has(m.user_id)) ?? []

  const handleAddMember = async () => {
    if (!selectedUserId) return
    await onAddMember(selectedUserId)
    setSelectedUserId("")
  }

  return (
    <DialogContent className="max-w-lg">
      <DialogHeader>
        <DialogTitle>Manage members - {group.name}</DialogTitle>
        <DialogDescription>
          Add or remove users from this group. Members inherit all role
          assignments for this group.
        </DialogDescription>
      </DialogHeader>
      <div className="space-y-4 py-4">
        {/* Add member section */}
        <div className="space-y-2">
          <Label>Add member</Label>
          <div className="flex gap-2">
            <Select value={selectedUserId} onValueChange={setSelectedUserId}>
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

        {/* Current members list */}
        <div className="space-y-2">
          <Label>Current members ({group.members?.length ?? 0})</Label>
          <ScrollArea className="h-[200px] rounded-md border">
            {group.members && group.members.length > 0 ? (
              <div className="p-4 space-y-2">
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
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => onRemoveMember(member.user_id)}
                      disabled={isRemovingMember}
                      className="text-rose-500 hover:text-rose-600"
                    >
                      <UserMinusIcon className="size-4" />
                    </Button>
                  </div>
                ))}
              </div>
            ) : (
              <div className="flex h-full items-center justify-center p-4">
                <p className="text-sm text-muted-foreground">No members yet</p>
              </div>
            )}
          </ScrollArea>
        </div>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChange(false)}>
          Done
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}

const toolbarProps: DataTableToolbarProps<GroupReadWithMembers> = {
  filterProps: {
    placeholder: "Filter groups...",
    column: "name",
  },
}
