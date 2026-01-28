"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { FolderIcon, GlobeIcon, PlusIcon, UserIcon } from "lucide-react"
import { useState } from "react"
import type { UserRoleAssignmentReadWithDetails } from "@/client"
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
  useOrgMembers,
  useRbacRoles,
  useRbacUserAssignments,
  useWorkspaceManager,
} from "@/lib/hooks"

export function OrgRbacUserAssignments() {
  const [selectedAssignment, setSelectedAssignment] =
    useState<UserRoleAssignmentReadWithDetails | null>(null)
  const [isCreateOpen, setIsCreateOpen] = useState(false)
  const [isEditOpen, setIsEditOpen] = useState(false)
  const {
    userAssignments,
    isLoading,
    error,
    createUserAssignment,
    createUserAssignmentIsPending,
    updateUserAssignment,
    updateUserAssignmentIsPending,
    deleteUserAssignment,
    deleteUserAssignmentIsPending,
  } = useRbacUserAssignments()

  const handleCreateAssignment = async (
    userId: string,
    roleId: string,
    workspaceId: string | null
  ) => {
    await createUserAssignment({
      user_id: userId,
      role_id: roleId,
      workspace_id: workspaceId,
    })
    setIsCreateOpen(false)
  }

  const handleUpdateAssignment = async (
    assignmentId: string,
    roleId: string
  ) => {
    await updateUserAssignment({ assignmentId, role_id: roleId })
    setIsEditOpen(false)
    setSelectedAssignment(null)
  }

  const handleDeleteAssignment = async () => {
    if (selectedAssignment) {
      await deleteUserAssignment(selectedAssignment.id)
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
              User assignments grant roles directly to individual users,
              optionally scoped to specific workspaces.
            </p>
            <DialogTrigger asChild>
              <Button size="sm" onClick={() => setIsCreateOpen(true)}>
                <PlusIcon className="mr-2 size-4" />
                Create assignment
              </Button>
            </DialogTrigger>
          </div>

          <DataTable
            data={userAssignments}
            isLoading={isLoading}
            error={error as Error | null}
            emptyMessage="No user assignments found"
            initialSortingState={[{ id: "user_email", desc: false }]}
            columns={[
              {
                accessorKey: "user_email",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="User"
                  />
                ),
                cell: ({ row }) => (
                  <div className="flex items-center gap-2">
                    <UserIcon className="size-4 text-muted-foreground" />
                    <span className="text-sm font-medium">
                      {row.getValue<string>("user_email")}
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
            <AlertDialogTitle>Delete user assignment</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to remove the{" "}
              <span className="font-semibold">
                {selectedAssignment?.role_name}
              </span>{" "}
              role from{" "}
              <span className="font-semibold">
                {selectedAssignment?.user_email}
              </span>
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
              disabled={deleteUserAssignmentIsPending}
            >
              {deleteUserAssignmentIsPending ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {isCreateOpen && (
        <UserAssignmentFormDialog
          title="Create user assignment"
          description="Assign a role directly to a user. Organization-wide assignments apply to all workspaces."
          onSubmit={handleCreateAssignment}
          isPending={createUserAssignmentIsPending}
          onOpenChange={(open) => {
            if (!open) setIsCreateOpen(false)
          }}
        />
      )}

      {isEditOpen && selectedAssignment && (
        <UserAssignmentEditDialog
          assignment={selectedAssignment}
          onSubmit={(roleId) =>
            handleUpdateAssignment(selectedAssignment.id, roleId)
          }
          isPending={updateUserAssignmentIsPending}
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

function UserAssignmentFormDialog({
  title,
  description,
  onSubmit,
  isPending,
  onOpenChange,
}: {
  title: string
  description: string
  onSubmit: (
    userId: string,
    roleId: string,
    workspaceId: string | null
  ) => Promise<void>
  isPending: boolean
  onOpenChange: (open: boolean) => void
}) {
  const [userId, setUserId] = useState("")
  const [roleId, setRoleId] = useState("")
  const [workspaceId, setWorkspaceId] = useState<string>("org-wide")

  const { orgMembers } = useOrgMembers()
  const { roles } = useRbacRoles()
  const { workspaces } = useWorkspaceManager()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!userId || !roleId) return
    await onSubmit(
      userId,
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
            <Label htmlFor="assignment-user">User</Label>
            <Select value={userId} onValueChange={setUserId}>
              <SelectTrigger id="assignment-user">
                <SelectValue placeholder="Select a user" />
              </SelectTrigger>
              <SelectContent>
                {orgMembers?.map((member) => (
                  <SelectItem key={member.user_id} value={member.user_id}>
                    {member.email}
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
          <Button type="submit" disabled={!userId || !roleId || isPending}>
            {isPending ? "Creating..." : "Create assignment"}
          </Button>
        </DialogFooter>
      </form>
    </DialogContent>
  )
}

function UserAssignmentEditDialog({
  assignment,
  onSubmit,
  isPending,
  onOpenChange,
}: {
  assignment: UserRoleAssignmentReadWithDetails
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
          <DialogTitle>Change user assignment</DialogTitle>
          <DialogDescription>
            Update the role for{" "}
            <span className="font-semibold">{assignment.user_email}</span>
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

const toolbarProps: DataTableToolbarProps<UserRoleAssignmentReadWithDetails> = {
  filterProps: {
    placeholder: "Filter user assignments...",
    column: "user_email",
  },
}
