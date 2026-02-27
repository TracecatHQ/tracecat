"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { useQueryClient } from "@tanstack/react-query"
import { BotIcon } from "lucide-react"
import { useCallback, useEffect, useMemo, useState } from "react"
import type { WorkspaceMember, WorkspaceRead } from "@/client"
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
  DialogTrigger,
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
import { useUpdateAgentPreset } from "@/hooks/use-agent-presets"
import {
  useWorkspaceMembers,
  useWorkspaceMutations,
} from "@/hooks/use-workspace"
import { useRbacRoles, useRbacUserAssignments } from "@/lib/hooks"
import { useScopes } from "@/providers/scopes"

const AGENT_PRESET_EMAIL_SUFFIX = "@agent-presets.example.com"
const DEFAULT_PRESET_ROLE_VALUE = "__default_preset_role__"

function isAgentPresetMember(member: WorkspaceMember): boolean {
  return member.email.endsWith(AGENT_PRESET_EMAIL_SUFFIX)
}

function getAgentPresetSlug(member: WorkspaceMember): string | null {
  if (!isAgentPresetMember(member)) {
    return null
  }
  return member.email.slice(0, -AGENT_PRESET_EMAIL_SUFFIX.length) || null
}

export function WorkspaceMembersTable({
  workspace,
}: {
  workspace: WorkspaceRead
}) {
  const queryClient = useQueryClient()
  const { hasScope } = useScopes()
  const canUpdateMembers = useScopeCheck("workspace:member:update")
  const canUpdateAnyAgentPreset = useScopeCheck("agent:preset:*:update")
  const canRemoveMembers = useScopeCheck("workspace:member:remove")
  const [selectedUser, setSelectedUser] = useState<WorkspaceMember | null>(null)
  const [isChangeRoleOpen, setIsChangeRoleOpen] = useState(false)
  const { removeMember } = useWorkspaceMutations()
  const { updateAgentPreset, updateAgentPresetIsPending } =
    useUpdateAgentPreset(workspace.id)
  const { members, membersLoading, membersError } = useWorkspaceMembers(
    workspace.id
  )
  const {
    userAssignments,
    isLoading: userAssignmentsIsLoading,
    updateUserAssignment,
    updateUserAssignmentIsPending,
    createUserAssignment,
    createUserAssignmentIsPending,
  } = useRbacUserAssignments({
    workspaceId: workspace.id,
    enabled: isChangeRoleOpen,
  })

  const handleChangeRole = useCallback(
    async (roleId: string) => {
      try {
        if (!selectedUser) {
          return toast({
            title: "No user selected",
            description: "Please select a user to change role",
          })
        }
        const selectedIsAgentPreset = isAgentPresetMember(selectedUser)
        if (!roleId) {
          return toast({
            title: "No role selected",
            description: "Please select a role before continuing.",
          })
        }
        if (!selectedIsAgentPreset && userAssignmentsIsLoading) {
          return toast({
            title: "Role data is loading",
            description: "Wait a moment and try changing the role again.",
          })
        }
        if (selectedIsAgentPreset) {
          await updateAgentPreset({
            presetId: selectedUser.user_id,
            assigned_role_id:
              roleId === DEFAULT_PRESET_ROLE_VALUE ? null : roleId,
          })
        } else {
          // Find the existing RBAC assignment for this user in this workspace
          const existingAssignment = userAssignments?.find(
            (a) =>
              a.user_id === selectedUser.user_id &&
              a.workspace_id === workspace.id
          )
          if (existingAssignment) {
            await updateUserAssignment({
              assignmentId: existingAssignment.id,
              role_id: roleId,
            })
          } else {
            // No existing assignment - create one
            await createUserAssignment({
              user_id: selectedUser.user_id,
              role_id: roleId,
              workspace_id: workspace.id,
            })
          }
        }
        await Promise.all([
          queryClient.invalidateQueries({
            queryKey: ["workspace", workspace.id, "members"],
          }),
          queryClient.invalidateQueries({
            queryKey: ["org-members"],
          }),
        ])
      } catch (error) {
        console.log("Failed to change role", error)
      } finally {
        setIsChangeRoleOpen(false)
        setSelectedUser(null)
      }
    },
    [
      selectedUser,
      userAssignments,
      workspace.id,
      updateUserAssignment,
      createUserAssignment,
      userAssignmentsIsLoading,
      queryClient,
      updateAgentPreset,
    ]
  )

  const isRoleMutationPending =
    updateUserAssignmentIsPending ||
    createUserAssignmentIsPending ||
    updateAgentPresetIsPending

  const canUpdateAgentPresetMember = useCallback(
    (member: WorkspaceMember): boolean => {
      if (canUpdateAnyAgentPreset === true) {
        return true
      }
      const presetSlug = getAgentPresetSlug(member)
      if (!presetSlug) {
        return false
      }
      return hasScope(`agent:preset:${presetSlug}:update`)
    },
    [canUpdateAnyAgentPreset, hasScope]
  )

  return (
    <Dialog open={isChangeRoleOpen} onOpenChange={setIsChangeRoleOpen}>
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
                  {isAgentPresetMember(row.original)
                    ? "-"
                    : row.getValue<WorkspaceMember["email"]>("email")}
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
                const member = row.original
                const isAgentPreset = isAgentPresetMember(member)
                const name = isAgentPreset
                  ? member.last_name || "Agent preset"
                  : [member.first_name, member.last_name]
                      .filter(Boolean)
                      .join(" ")
                return (
                  <div className="flex items-center gap-2 text-xs">
                    {isAgentPreset && (
                      <BotIcon className="size-3.5 text-muted-foreground" />
                    )}
                    <span>{name || "-"}</span>
                  </div>
                )
              },
              enableSorting: false,
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
                <div className="text-xs capitalize">
                  {row.getValue<string>("role_name")}
                </div>
              ),
              enableSorting: true,
              enableHiding: false,
            },

            {
              id: "actions",
              enableHiding: false,
              cell: ({ row }) => {
                const member = row.original
                const isAgentPreset = isAgentPresetMember(member)
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
                          navigator.clipboard.writeText(member.user_id)
                        }
                      >
                        {isAgentPreset
                          ? "Copy agent preset ID"
                          : "Copy user ID"}
                      </DropdownMenuItem>

                      {((isAgentPreset && canUpdateAgentPresetMember(member)) ||
                        (!isAgentPreset && canUpdateMembers)) && (
                        <DialogTrigger asChild>
                          <DropdownMenuItem
                            onClick={() => {
                              setSelectedUser(member)
                              setIsChangeRoleOpen(true)
                            }}
                          >
                            Change role
                          </DropdownMenuItem>
                        </DialogTrigger>
                      )}

                      {!isAgentPreset && canRemoveMembers && (
                        <AlertDialogTrigger asChild>
                          <DropdownMenuItem
                            className="text-rose-500 focus:text-rose-600"
                            onClick={() => {
                              setSelectedUser(member)
                              console.debug("Selected user", member)
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
                if (selectedUser && !isAgentPresetMember(selectedUser)) {
                  console.log("Removing member", selectedUser)
                  try {
                    await removeMember(selectedUser.user_id)
                  } catch (error) {
                    const description =
                      error instanceof Error
                        ? error.message
                        : "The request could not be completed."
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
      </AlertDialog>
      <ChangeUserRoleDialog
        open={isChangeRoleOpen}
        selectedUser={selectedUser}
        isSubmitting={isRoleMutationPending || userAssignmentsIsLoading}
        isAgentPresetMember={
          selectedUser ? isAgentPresetMember(selectedUser) : false
        }
        setOpen={setIsChangeRoleOpen}
        onConfirm={handleChangeRole}
      />
    </Dialog>
  )
}

function ChangeUserRoleDialog({
  open,
  selectedUser,
  isSubmitting,
  isAgentPresetMember,
  setOpen,
  onConfirm,
}: {
  open: boolean
  selectedUser: WorkspaceMember | null
  isSubmitting: boolean
  isAgentPresetMember: boolean
  setOpen: (open: boolean) => void
  onConfirm: (roleId: string) => void
}) {
  const { roles, isLoading: rolesIsLoading } = useRbacRoles({
    enabled: open,
  })
  const workspaceRoles = useMemo(
    () => roles.filter((r) => !r.slug || r.slug.startsWith("workspace-")),
    [roles]
  )
  const roleOptions = useMemo(
    () =>
      isAgentPresetMember
        ? [
            {
              id: DEFAULT_PRESET_ROLE_VALUE,
              name: "Default preset role",
            },
            ...workspaceRoles,
          ]
        : workspaceRoles,
    [isAgentPresetMember, workspaceRoles]
  )
  const [selectedRoleId, setSelectedRoleId] = useState<string>("")

  useEffect(() => {
    if (!open) {
      setSelectedRoleId("")
      return
    }
    if (
      isAgentPresetMember &&
      selectedUser?.role_name === "Default preset role"
    ) {
      setSelectedRoleId(DEFAULT_PRESET_ROLE_VALUE)
      return
    }
    const match = workspaceRoles.find((r) => r.name === selectedUser?.role_name)
    setSelectedRoleId(match?.id ?? "")
  }, [isAgentPresetMember, open, selectedUser, workspaceRoles])

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>
          {isAgentPresetMember
            ? "Change agent preset role"
            : "Change user role"}
        </DialogTitle>
        <DialogDescription>
          Select a new role for{" "}
          {isAgentPresetMember
            ? selectedUser?.last_name || "Agent preset"
            : selectedUser?.email}
        </DialogDescription>
      </DialogHeader>
      <Select
        value={selectedRoleId}
        onValueChange={(value) => setSelectedRoleId(value)}
      >
        <SelectTrigger disabled={rolesIsLoading}>
          <SelectValue placeholder="Select a role" />
        </SelectTrigger>
        <SelectContent>
          {roleOptions.map((role) => (
            <SelectItem key={role.id} value={role.id}>
              {role.name}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
      <DialogFooter>
        <Button variant="outline" onClick={() => setOpen(false)}>
          Cancel
        </Button>
        <Button
          disabled={!selectedRoleId || rolesIsLoading || isSubmitting}
          onClick={() => onConfirm(selectedRoleId)}
        >
          Change role
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}

const defaultToolbarProps: DataTableToolbarProps<WorkspaceMember> = {
  filterProps: {
    placeholder: "Filter members by email...",
    column: "email",
  },
}
