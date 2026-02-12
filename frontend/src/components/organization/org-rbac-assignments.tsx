"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import {
  FolderIcon,
  GlobeIcon,
  PlusIcon,
  SearchIcon,
  UsersIcon,
} from "lucide-react"
import { useMemo, useState } from "react"
import type { GroupRoleAssignmentReadWithDetails } from "@/client"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import {
  useRbacAssignments,
  useRbacGroups,
  useRbacRoles,
  useWorkspaceManager,
} from "@/lib/hooks"

export function OrgRbacAssignments() {
  const [selectedAssignment, setSelectedAssignment] =
    useState<GroupRoleAssignmentReadWithDetails | null>(null)
  const [expandedAssignmentId, setExpandedAssignmentId] = useState<
    string | null
  >(null)
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [isEditOpen, setIsEditOpen] = useState(false)
  const [searchQuery, setSearchQuery] = useState("")
  const {
    assignments,
    isLoading,
    error,
    createAssignment,
    createAssignmentIsPending,
    updateAssignment,
    updateAssignmentIsPending,
    deleteAssignment,
    deleteAssignmentIsPending,
  } = useRbacAssignments()

  const { roles } = useRbacRoles()

  const filteredAssignments = useMemo(() => {
    if (!searchQuery.trim()) return assignments
    const query = searchQuery.toLowerCase()
    return assignments.filter(
      (a) =>
        a.group_name.toLowerCase().includes(query) ||
        a.role_name.toLowerCase().includes(query) ||
        a.workspace_name?.toLowerCase().includes(query)
    )
  }, [assignments, searchQuery])

  const handleCreateAssignment = async (
    groupId: string,
    roleId: string,
    workspaceId: string | null
  ) => {
    await createAssignment({
      group_id: groupId,
      role_id: roleId,
      workspace_id: workspaceId,
    })
    setIsCreateOpen(false)
  }

  const handleUpdateAssignment = async (
    assignmentId: string,
    roleId: string
  ) => {
    await updateAssignment({ assignmentId, role_id: roleId })
    setIsEditOpen(false)
    setSelectedAssignment(null)
  }

  const handleDeleteAssignment = async () => {
    if (selectedAssignment) {
      await deleteAssignment(selectedAssignment.id)
      setSelectedAssignment(null)
    }
  }

  // Get role details for expanded view
  const getRoleScopes = (roleId: string) => {
    const role = roles.find((r) => r.id === roleId)
    return role?.scopes ?? []
  }

  if (error) {
    return (
      <div className="flex items-center justify-center py-12 text-sm text-destructive">
        Failed to load assignments
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
          setSelectedAssignment(null)
        }
      }}
    >
      <AlertDialog
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setSelectedAssignment(null)
          }
        }}
      >
        <div className="space-y-4">
          <RbacListHeader
            left={
              <div className="relative">
                <SearchIcon className="absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
                <Input
                  placeholder="Search assignments..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="h-9 w-[250px] pl-8"
                />
              </div>
            }
            right={
              <DialogTrigger asChild>
                <Button size="sm" onClick={() => setIsCreateOpen(true)}>
                  <PlusIcon className="mr-2 size-4" />
                  Create assignment
                </Button>
              </DialogTrigger>
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
          ) : filteredAssignments.length === 0 ? (
            <RbacListContainer>
              <RbacListEmpty
                message={
                  searchQuery
                    ? "No assignments match your search"
                    : "No assignments found"
                }
              />
            </RbacListContainer>
          ) : (
            <RbacListContainer>
              {filteredAssignments.map((assignment) => (
                <RbacListItem
                  key={assignment.id}
                  icon={<UsersIcon className="size-4" />}
                  title={assignment.group_name}
                  subtitle={
                    <span className="flex items-center gap-1.5">
                      <Badge variant="secondary" className="text-[10px]">
                        {assignment.role_name}
                      </Badge>
                      <span className="text-muted-foreground">·</span>
                      {assignment.workspace_name ? (
                        <span className="flex items-center gap-1">
                          <FolderIcon className="size-3" />
                          {assignment.workspace_name}
                        </span>
                      ) : (
                        <span className="flex items-center gap-1 text-blue-600 dark:text-blue-400">
                          <GlobeIcon className="size-3" />
                          Organization-wide
                        </span>
                      )}
                    </span>
                  }
                  isExpanded={expandedAssignmentId === assignment.id}
                  onExpandedChange={(expanded) =>
                    setExpandedAssignmentId(expanded ? assignment.id : null)
                  }
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
                          onClick={() =>
                            navigator.clipboard.writeText(assignment.id)
                          }
                        >
                          Copy assignment ID
                        </DropdownMenuItem>
                        <DropdownMenuSeparator />
                        <DialogTrigger asChild>
                          <DropdownMenuItem
                            onClick={() => {
                              setSelectedAssignment(assignment)
                              setIsEditOpen(true)
                            }}
                          >
                            Change role
                          </DropdownMenuItem>
                        </DialogTrigger>
                        <AlertDialogTrigger asChild>
                          <DropdownMenuItem
                            className="text-rose-500 focus:text-rose-600"
                            onClick={() => setSelectedAssignment(assignment)}
                          >
                            Delete assignment
                          </DropdownMenuItem>
                        </AlertDialogTrigger>
                      </DropdownMenuContent>
                    </DropdownMenu>
                  }
                >
                  <div className="space-y-3">
                    <RbacDetailRow label="Group">
                      <span className="flex items-center gap-1.5">
                        <UsersIcon className="size-3 text-muted-foreground" />
                        {assignment.group_name}
                      </span>
                    </RbacDetailRow>
                    <RbacDetailRow label="Role">
                      <Badge variant="secondary">{assignment.role_name}</Badge>
                    </RbacDetailRow>
                    <RbacDetailRow label="Scope">
                      {assignment.workspace_name ? (
                        <span className="flex items-center gap-1.5">
                          <FolderIcon className="size-3 text-muted-foreground" />
                          {assignment.workspace_name}
                        </span>
                      ) : (
                        <span className="flex items-center gap-1.5 text-blue-600 dark:text-blue-400">
                          <GlobeIcon className="size-3" />
                          Organization-wide
                        </span>
                      )}
                    </RbacDetailRow>
                    {getRoleScopes(assignment.role_id).length > 0 && (
                      <RbacDetailRow label="Permissions">
                        <div className="flex flex-wrap gap-1">
                          {getRoleScopes(assignment.role_id)
                            .slice(0, 5)
                            .map((scope) => (
                              <code
                                key={scope.id}
                                className="rounded bg-muted/60 px-1.5 py-0.5 text-[10px] font-mono"
                              >
                                {scope.name}
                              </code>
                            ))}
                          {getRoleScopes(assignment.role_id).length > 5 && (
                            <span className="text-[10px] text-muted-foreground">
                              +{getRoleScopes(assignment.role_id).length - 5}{" "}
                              more
                            </span>
                          )}
                        </div>
                      </RbacDetailRow>
                    )}
                  </div>
                </RbacListItem>
              ))}
            </RbacListContainer>
          )}
        </div>

        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete assignment</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to remove the{" "}
              <span className="font-semibold">
                {selectedAssignment?.role_name}
              </span>{" "}
              role from the{" "}
              <span className="font-semibold">
                {selectedAssignment?.group_name}
              </span>{" "}
              group
              {selectedAssignment?.workspace_name
                ? ` in the ${selectedAssignment.workspace_name} workspace`
                : " at the organization level"}
              ? This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={handleDeleteAssignment}
              disabled={deleteAssignmentIsPending}
            >
              {deleteAssignmentIsPending ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {isCreateOpen && (
        <AssignmentFormDialog
          title="Create assignment"
          description="Assign a role to a group. Organization-wide assignments apply to all workspaces."
          onSubmit={handleCreateAssignment}
          isPending={createAssignmentIsPending}
          onOpenChange={(open) => {
            if (!open) setIsCreateOpen(false)
          }}
        />
      )}

      {isEditOpen && selectedAssignment && (
        <AssignmentEditDialog
          assignment={selectedAssignment}
          onSubmit={(roleId) =>
            handleUpdateAssignment(selectedAssignment.id, roleId)
          }
          isPending={updateAssignmentIsPending}
          onOpenChange={(open) => {
            if (!open) {
              setIsEditOpen(false)
              setSelectedAssignment(null)
            }
          }}
        />
      )}
    </Dialog>
  )
}

function AssignmentFormDialog({
  title,
  description,
  onSubmit,
  isPending,
  onOpenChange,
}: {
  title: string
  description: string
  onSubmit: (
    groupId: string,
    roleId: string,
    workspaceId: string | null
  ) => Promise<void>
  isPending: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [groupId, setGroupId] = useState("")
  const [roleId, setRoleId] = useState("")
  const [workspaceId, setWorkspaceId] = useState<string>("org-wide")

  const { groups } = useRbacGroups()
  const { roles } = useRbacRoles()
  const { workspaces } = useWorkspaceManager()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!groupId || !roleId) return
    await onSubmit(
      groupId,
      roleId,
      workspaceId === "org-wide" ? null : workspaceId
    )
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
            <Label htmlFor="assignment-group">Group</Label>
            <Select value={groupId} onValueChange={setGroupId}>
              <SelectTrigger id="assignment-group">
                <SelectValue placeholder="Select a group" />
              </SelectTrigger>
              <SelectContent>
                {groups.map((group) => (
                  <SelectItem key={group.id} value={group.id}>
                    {group.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-2">
            <Label htmlFor="assignment-role">Role</Label>
            <Select value={roleId} onValueChange={setRoleId}>
              <SelectTrigger id="assignment-role">
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
          </div>

          <div className="space-y-2">
            <Label htmlFor="assignment-workspace">Scope</Label>
            <Select value={workspaceId} onValueChange={setWorkspaceId}>
              <SelectTrigger id="assignment-workspace">
                <SelectValue placeholder="Select scope" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="org-wide">
                  <div className="flex items-center gap-2">
                    <GlobeIcon className="size-4 text-blue-500" />
                    Organization-wide
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
            <p className="text-xs text-muted-foreground">
              Organization-wide assignments apply to all workspaces. Workspace
              assignments only apply within that workspace.
            </p>
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
          <Button type="submit" disabled={!groupId || !roleId || isPending}>
            {isPending ? "Creating..." : "Create assignment"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  )
}

function AssignmentEditDialog({
  assignment,
  onSubmit,
  isPending,
  onOpenChange,
}: {
  assignment: GroupRoleAssignmentReadWithDetails
  onSubmit: (roleId: string) => Promise<void>
  isPending: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [roleId, setRoleId] = useState(assignment.role_id)
  const { roles } = useRbacRoles()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!roleId) return
    await onSubmit(roleId)
  }

  return (
    <DialogContent>
      <form onSubmit={handleSubmit}>
        <DialogHeader>
          <DialogTitle>Change role assignment</DialogTitle>
          <DialogDescription>
            Update the role for{" "}
            <span className="font-semibold">{assignment.group_name}</span>
            {assignment.workspace_name
              ? ` in ${assignment.workspace_name}`
              : " (organization-wide)"}
            .
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4 py-4">
          <div className="space-y-2">
            <Label htmlFor="edit-role">Role</Label>
            <Select value={roleId} onValueChange={setRoleId}>
              <SelectTrigger id="edit-role">
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
          <Button type="submit" disabled={!roleId || isPending}>
            {isPending ? "Saving..." : "Save changes"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  )
}
