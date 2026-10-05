"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useMemo, useState } from "react"
import type {
  WorkspaceAssignableRole,
  WorkspaceMember,
  WorkspaceRead,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
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
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import { useAuth } from "@/hooks/use-auth"
import {
  useWorkspaceAssignableRoles,
  useWorkspaceMembers,
  useWorkspaceMutations,
} from "@/hooks/use-workspace"
import { getApiErrorDetail } from "@/lib/errors"

/**
 * Whether the caller may change this member's role from the workspace.
 *
 * Group and org-wide grants live in org settings. A member whose current role
 * is not assignable holds scopes the caller lacks.
 */
export function canChangeRole(
  member: WorkspaceMember,
  caller: {
    canUpdateMembers: boolean
    currentUserId: string | undefined
    assignableRoleIds: ReadonlySet<string>
  }
): boolean {
  return (
    caller.canUpdateMembers &&
    !member.via_group &&
    !member.org_wide &&
    member.user_id !== caller.currentUserId &&
    caller.assignableRoleIds.has(member.role_id)
  )
}

export function WorkspaceMembersTable({
  workspace,
}: {
  workspace: WorkspaceRead
}) {
  const canRemoveMembers = useScopeCheck("workspace:member:remove")
  const canUpdateMembers = useScopeCheck("workspace:member:update") === true
  const [selectedUser, setSelectedUser] = useState<WorkspaceMember | null>(null)
  const [roleTarget, setRoleTarget] = useState<WorkspaceMember | null>(null)
  const { user } = useAuth()
  const { removeMember } = useWorkspaceMutations()
  const { members, membersLoading, membersError } = useWorkspaceMembers(
    workspace.id
  )
  const { assignableRoles } = useWorkspaceAssignableRoles(workspace.id, {
    enabled: canUpdateMembers,
  })
  const assignableRoleIds = useMemo(
    () => new Set(assignableRoles?.map((role) => role.id)),
    [assignableRoles]
  )

  return (
    <AlertDialog
      onOpenChange={(isOpen) => {
        if (!isOpen) {
          setSelectedUser(null)
        }
      }}
    >
      <DataTable
        data={members}
        isLoading={membersLoading}
        error={membersError}
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
                {row.getValue<WorkspaceMember["email"]>("email")}
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
                {row.getValue<WorkspaceMember["first_name"]>("first_name") ||
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
                {row.getValue<WorkspaceMember["last_name"]>("last_name") || "-"}
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
              <div className="flex items-center gap-2 text-xs">
                <span className="capitalize">
                  {row.getValue<string>("role_name")}
                </span>
              </div>
            ),
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

                    {canUpdateMembers && (
                      <DropdownMenuItem
                        disabled={
                          !canChangeRole(row.original, {
                            canUpdateMembers,
                            currentUserId: user?.id,
                            assignableRoleIds,
                          })
                        }
                        onClick={() => setRoleTarget(row.original)}
                      >
                        Change role
                      </DropdownMenuItem>
                    )}

                    {canRemoveMembers && (
                      <AlertDialogTrigger asChild>
                        <DropdownMenuItem
                          className="text-rose-500 focus:text-rose-600"
                          onClick={() => {
                            setSelectedUser(row.original)
                            console.debug("Selected user", row.original)
                          }}
                        >
                          Remove from workspace
                        </DropdownMenuItem>
                      </AlertDialogTrigger>
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
            Are you sure you want to remove this user from the workspace? This
            action cannot be undone.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={async () => {
              if (selectedUser) {
                console.log("Removing member", selectedUser)
                try {
                  await removeMember(selectedUser.user_id)
                } catch (error) {
                  const description =
                    getApiErrorDetail(error) ??
                    "The request could not be completed."
                  console.error("Failed to remove member", error)
                  toast({
                    title: "Failed to remove member",
                    description,
                    variant: "destructive",
                  })
                }
              }
              setSelectedUser(null)
            }}
          >
            Confirm
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
      <ChangeRoleDialog
        member={roleTarget}
        roles={assignableRoles ?? []}
        onClose={() => setRoleTarget(null)}
      />
    </AlertDialog>
  )
}

function ChangeRoleDialog({
  member,
  roles,
  onClose,
}: {
  member: WorkspaceMember | null
  roles: WorkspaceAssignableRole[]
  onClose: () => void
}) {
  return (
    <Dialog open={member !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="w-[calc(100%-2rem)] max-w-md">
        {/* Keyed so the selection resets for each member. */}
        {member && (
          <ChangeRoleForm
            key={member.user_id}
            member={member}
            roles={roles}
            onClose={onClose}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function ChangeRoleForm({
  member,
  roles,
  onClose,
}: {
  member: WorkspaceMember
  roles: WorkspaceAssignableRole[]
  onClose: () => void
}) {
  const { updateMemberRole, updateRolePending } = useWorkspaceMutations()
  const [roleId, setRoleId] = useState(member.role_id)

  const onConfirm = async () => {
    try {
      await updateMemberRole({ userId: member.user_id, roleId })
      toast({
        title: "Role updated",
        description: `${member.email} now has the ${roles.find((role) => role.id === roleId)?.name ?? "selected"} role.`,
      })
      onClose()
    } catch (error) {
      console.error("Failed to change role", error)
      toast({
        title: "Failed to change role",
        description:
          getApiErrorDetail(error) ?? "The request could not be completed.",
        variant: "destructive",
      })
    }
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>Change role</DialogTitle>
        <DialogDescription>
          Select a new workspace role for {member.email}.
        </DialogDescription>
      </DialogHeader>
      <Select value={roleId} onValueChange={setRoleId}>
        <SelectTrigger aria-label="Role">
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
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose}>
          Cancel
        </Button>
        <Button
          type="button"
          disabled={updateRolePending || roleId === member.role_id}
          onClick={onConfirm}
        >
          {updateRolePending ? "Saving..." : "Change role"}
        </Button>
      </DialogFooter>
    </>
  )
}

const defaultToolbarProps: DataTableToolbarProps<WorkspaceMember> = {
  filterProps: {
    placeholder: "Filter users by email...",
    column: "email",
  },
}
