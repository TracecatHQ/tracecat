"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { FolderIcon, GlobeIcon, PlusIcon, UsersIcon } from "lucide-react"
import { useState } from "react"
import type { GroupAssignmentReadWithDetails } from "@/client"
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
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import {
  useRbacAssignments,
  useRbacGroups,
  useRbacRoles,
  useWorkspaceManager,
} from "@/lib/hooks"

export function OrgRbacAssignments() {
  const [selectedAssignment, setSelectedAssignment] =
    useState<GroupAssignmentReadWithDetails | null>(null)
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [isEditOpen, setIsEditOpen] = useState(false)
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
          <div className="flex items-center justify-between">
            <p className="text-sm text-muted-foreground">
              Assignments connect groups to roles, optionally scoped to specific
              workspaces.
            </p>
            <DialogTrigger asChild>
              <Button size="sm" onClick={() => setIsCreateOpen(true)}>
                <PlusIcon className="mr-2 size-4" />
                Create assignment
              </Button>
            </DialogTrigger>
          </div>

          <DataTable
            data={assignments}
            isLoading={isLoading}
            error={error as Error | null}
            emptyMessage="No assignments found"
            initialSortingState={[{ id: "group_name", desc: false }]}
            columns={[
              {
                accessorKey: "group_name",
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
                      {row.getValue<string>("group_name")}
                    </span>
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
                  <Badge variant="secondary">
                    {row.getValue<string>("role_name")}
                  </Badge>
                ),
                enableSorting: true,
                enableHiding: false,
              },
              {
                accessorKey: "workspace_name",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Scope"
                  />
                ),
                cell: ({ row }) => {
                  const workspaceName = row.getValue<string | null>(
                    "workspace_name"
                  )
                  if (!workspaceName) {
                    return (
                      <div className="flex items-center gap-1.5 text-xs">
                        <GlobeIcon className="size-3.5 text-blue-500" />
                        <span className="font-medium text-blue-600 dark:text-blue-400">
                          Organization-wide
                        </span>
                      </div>
                    )
                  }
                  return (
                    <div className="flex items-center gap-1.5 text-xs">
                      <FolderIcon className="size-3.5 text-muted-foreground" />
                      <span>{workspaceName}</span>
                    </div>
                  )
                },
                enableSorting: true,
                enableHiding: true,
              },
              {
                id: "actions",
                enableHiding: false,
                cell: ({ row }) => {
                  const assignment = row.original

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
                  )
                },
              },
            ]}
            toolbarProps={toolbarProps}
          />
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
  assignment: GroupAssignmentReadWithDetails
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

const toolbarProps: DataTableToolbarProps<GroupAssignmentReadWithDetails> = {
  filterProps: {
    placeholder: "Filter assignments...",
    column: "group_name",
  },
}
