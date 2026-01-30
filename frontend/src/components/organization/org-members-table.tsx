"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import {
  FolderIcon,
  GlobeIcon,
  PlusIcon,
  ShieldIcon,
  Trash2Icon,
} from "lucide-react"
import { useState } from "react"
import type { OrgMemberRead } from "@/client"
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
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Label } from "@/components/ui/label"
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useAuth } from "@/hooks/use-auth"
import { getRelativeTime } from "@/lib/event-history"
import {
  useOrgMembers,
  useRbacRoles,
  useRbacUserAssignments,
  useWorkspaceManager,
} from "@/lib/hooks"

function UserRolesCell({ userId }: { userId: string }) {
  const { userAssignments } = useRbacUserAssignments({ userId })

  if (userAssignments.length === 0) {
    return <span className="text-muted-foreground">-</span>
  }

  // Get unique role names
  const roleNames = [...new Set(userAssignments.map((a) => a.role_name))]

  return (
    <div className="flex flex-wrap gap-1">
      {roleNames.slice(0, 2).map((roleName) => (
        <Badge key={roleName} variant="secondary" className="text-[10px]">
          {roleName}
        </Badge>
      ))}
      {roleNames.length > 2 && (
        <span className="text-[10px] text-muted-foreground">
          +{roleNames.length - 2}
        </span>
      )}
    </div>
  )
}

export function OrgMembersTable() {
  const [selectedMember, setSelectedMember] = useState<OrgMemberRead | null>(
    null
  )
  const [isManageRolesOpen, setIsManageRolesOpen] = useState(false)
  const { user } = useAuth()
  const { orgMembers, deleteOrgMember } = useOrgMembers()

  const handleRemoveMember = async () => {
    if (selectedMember) {
      console.log("Removing member", selectedMember)
      try {
        await deleteOrgMember({
          userId: selectedMember.user_id,
        })
      } catch (error) {
        console.error("Failed to remove member", error)
      } finally {
        setSelectedMember(null)
      }
    }
  }

  return (
    <Dialog open={isManageRolesOpen} onOpenChange={setIsManageRolesOpen}>
      <AlertDialog
        onOpenChange={(isOpen) => {
          if (!isOpen) {
            setSelectedMember(null)
          }
        }}
      >
        <DataTable
          data={orgMembers}
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
              accessorKey: "first_name",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="First name"
                />
              ),
              cell: ({ row }) => (
                <div className="text-xs">
                  {row.getValue<OrgMemberRead["first_name"]>("first_name") ||
                    "-"}
                </div>
              ),
              enableSorting: true,
              enableHiding: false,
            },
            {
              accessorKey: "last_name",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Last name"
                />
              ),
              cell: ({ row }) => (
                <div className="text-xs">
                  {row.getValue<OrgMemberRead["last_name"]>("last_name") || "-"}
                </div>
              ),
              enableSorting: true,
              enableHiding: false,
            },
            {
              id: "roles",
              header: () => <span className="text-xs">Roles</span>,
              cell: ({ row }) => (
                <div className="text-xs">
                  <UserRolesCell userId={row.original.user_id} />
                </div>
              ),
              enableSorting: false,
              enableHiding: false,
            },
            {
              accessorKey: "is_active",
              header: ({ column }) => (
                <DataTableColumnHeader
                  className="text-xs"
                  column={column}
                  title="Active"
                />
              ),
              cell: ({ row }) => (
                <div className="text-xs capitalize">
                  {row.getValue<OrgMemberRead["is_active"]>("is_active")
                    ? "Yes"
                    : "No"}
                </div>
              ),
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
                  row.getValue<OrgMemberRead["last_login_at"]>("last_login_at")
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
                return (
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" className="size-8 p-0">
                        <span className="sr-only">Open menu</span>
                        <DotsHorizontalIcon className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent>
                      <DropdownMenuItem
                        onClick={() =>
                          navigator.clipboard.writeText(row.original.user_id)
                        }
                      >
                        Copy user ID
                      </DropdownMenuItem>

                      {user?.isPrivileged() && (
                        <DropdownMenuGroup>
                          <DropdownMenuSeparator />
                          <DropdownMenuItem
                            onClick={() => {
                              setSelectedMember(row.original)
                              setIsManageRolesOpen(true)
                            }}
                          >
                            <ShieldIcon className="mr-2 size-4" />
                            Manage roles
                          </DropdownMenuItem>

                          <AlertDialogTrigger asChild>
                            <DropdownMenuItem
                              className="text-rose-500 focus:text-rose-600"
                              onClick={() => {
                                setSelectedMember(row.original)
                                console.debug("Selected user", row.original)
                              }}
                            >
                              Remove from organization
                            </DropdownMenuItem>
                          </AlertDialogTrigger>
                        </DropdownMenuGroup>
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
            <AlertDialogTitle>Remove user</AlertDialogTitle>
            <AlertDialogDescription>
              Are you sure you want to remove this user from the organization?
              This action cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancel</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              onClick={handleRemoveMember}
            >
              Confirm
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {selectedMember && (
        <ManageUserRolesDialog
          member={selectedMember}
          onOpenChange={setIsManageRolesOpen}
        />
      )}
    </Dialog>
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

  const {
    userAssignments,
    createUserAssignment,
    createUserAssignmentIsPending,
    deleteUserAssignment,
    deleteUserAssignmentIsPending,
  } = useRbacUserAssignments({ userId: member.user_id })
  const { roles } = useRbacRoles()
  const { workspaces } = useWorkspaceManager()

  const handleAddRole = async () => {
    if (!roleId) return
    await createUserAssignment({
      user_id: member.user_id,
      role_id: roleId,
      workspace_id: workspaceId === "org-wide" ? null : workspaceId,
    })
    setRoleId("")
    setWorkspaceId("org-wide")
  }

  const handleRemoveRole = async (assignmentId: string) => {
    await deleteUserAssignment(assignmentId)
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
              disabled={!roleId || createUserAssignmentIsPending}
            >
              <PlusIcon className="size-4" />
            </Button>
          </div>
        </div>

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
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() => handleRemoveRole(assignment.id)}
                      disabled={deleteUserAssignmentIsPending}
                      className="text-rose-500 hover:text-rose-600"
                    >
                      <Trash2Icon className="size-4" />
                    </Button>
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
      <DialogFooter>
        <Button variant="outline" onClick={() => onOpenChange(false)}>
          Done
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}

const defaultToolbarProps: DataTableToolbarProps<OrgMemberRead> = {
  filterProps: {
    placeholder: "Filter users by email...",
    column: "email",
  },
}
