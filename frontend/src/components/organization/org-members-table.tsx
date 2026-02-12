"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { DotsHorizontalIcon, PlusIcon } from "@radix-ui/react-icons"
import { XIcon } from "lucide-react"
import { useCallback, useEffect, useState } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import { z } from "zod"
import {
  type OrgMemberRead,
  type OrgRole,
  organizationGetInvitationToken,
  type UserRole,
  type WorkspaceRole,
  workspacesCreateWorkspaceInvitation,
  workspacesUpdateWorkspaceMembership,
} from "@/client"
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
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import { useOrgMembership } from "@/hooks/use-org-membership"
import { getRelativeTime } from "@/lib/event-history"
import {
  useOrgMembers,
  useOrgMemberWorkspaces,
  useWorkspaceManager,
} from "@/lib/hooks"

const invitationFormSchema = z.object({
  email: z.string().email("Invalid email address"),
  role: z.enum(["member", "admin", "owner"]),
  workspace_assignments: z.array(
    z.object({
      workspace_id: z.string().uuid(),
      role: z.enum(["viewer", "editor", "admin"]),
    })
  ),
})

type InvitationFormValues = z.infer<typeof invitationFormSchema>

function InviteMemberDialogButton() {
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false)
  const { canAdministerOrg } = useOrgMembership()
  const { createInvitation, createInvitationIsPending } = useOrgMembers()
  const { workspaces } = useWorkspaceManager()

  const form = useForm<InvitationFormValues>({
    resolver: zodResolver(invitationFormSchema),
    defaultValues: {
      email: "",
      role: "member",
      workspace_assignments: [],
    },
  })

  const { fields, append, remove } = useFieldArray({
    control: form.control,
    name: "workspace_assignments",
  })

  const selectedWorkspaceIds = form
    .watch("workspace_assignments")
    .map((a) => a.workspace_id)
  const availableWorkspaces = (workspaces ?? []).filter(
    (ws) => !selectedWorkspaceIds.includes(ws.id)
  )

  const handleCreateInvitation = async (values: InvitationFormValues) => {
    try {
      await createInvitation({
        email: values.email,
        role: values.role as OrgRole,
        workspace_assignments: values.workspace_assignments,
      })
      form.reset()
      setIsCreateDialogOpen(false)
    } catch {
      // Error handled in hook
    }
  }

  if (!canAdministerOrg) {
    return null
  }

  return (
    <Dialog
      open={isCreateDialogOpen}
      onOpenChange={(open) => {
        setIsCreateDialogOpen(open)
        if (!open) form.reset()
      }}
    >
      <DialogTrigger asChild>
        <Button size="sm">
          <PlusIcon className="mr-2 size-4" />
          Invite member
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Invite member</DialogTitle>
          <DialogDescription>
            Send an invitation to join this organization.
          </DialogDescription>
        </DialogHeader>
        <Form {...form}>
          <form
            onSubmit={form.handleSubmit(handleCreateInvitation)}
            className="space-y-4"
          >
            <FormField
              control={form.control}
              name="email"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Email</FormLabel>
                  <FormControl>
                    <Input
                      placeholder="user@example.com"
                      type="email"
                      {...field}
                    />
                  </FormControl>
                  <FormDescription>
                    The email address of the person to invite.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <FormField
              control={form.control}
              name="role"
              render={({ field }) => (
                <FormItem>
                  <FormLabel>Organization role</FormLabel>
                  <Select
                    onValueChange={field.onChange}
                    defaultValue={field.value}
                  >
                    <FormControl>
                      <SelectTrigger>
                        <SelectValue placeholder="Select a role" />
                      </SelectTrigger>
                    </FormControl>
                    <SelectContent>
                      <SelectItem value="member">Member</SelectItem>
                      <SelectItem value="admin">Admin</SelectItem>
                      <SelectItem value="owner">Owner</SelectItem>
                    </SelectContent>
                  </Select>
                  <FormDescription>
                    The role to assign when the invitation is accepted.
                  </FormDescription>
                  <FormMessage />
                </FormItem>
              )}
            />
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <Label className="text-sm font-medium">
                  Workspace assignments
                </Label>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={availableWorkspaces.length === 0}
                  onClick={() => append({ workspace_id: "", role: "editor" })}
                >
                  Add
                </Button>
              </div>
              {fields.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  No workspace assignments. The user will only be invited to the
                  organization.
                </p>
              )}
              {fields.map((field, index) => (
                <div key={field.id} className="flex items-center gap-2">
                  <Select
                    value={form.watch(
                      `workspace_assignments.${index}.workspace_id`
                    )}
                    onValueChange={(value) =>
                      form.setValue(
                        `workspace_assignments.${index}.workspace_id`,
                        value
                      )
                    }
                  >
                    <SelectTrigger className="flex-1">
                      <SelectValue placeholder="Select workspace" />
                    </SelectTrigger>
                    <SelectContent>
                      {(workspaces ?? [])
                        .filter(
                          (ws) =>
                            !selectedWorkspaceIds.includes(ws.id) ||
                            ws.id ===
                              form.watch(
                                `workspace_assignments.${index}.workspace_id`
                              )
                        )
                        .map((ws) => (
                          <SelectItem key={ws.id} value={ws.id}>
                            {ws.name}
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                  <Select
                    value={form.watch(`workspace_assignments.${index}.role`)}
                    onValueChange={(value) =>
                      form.setValue(
                        `workspace_assignments.${index}.role`,
                        value as "viewer" | "editor" | "admin"
                      )
                    }
                  >
                    <SelectTrigger className="w-28">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="viewer">Viewer</SelectItem>
                      <SelectItem value="editor">Editor</SelectItem>
                      <SelectItem value="admin">Admin</SelectItem>
                    </SelectContent>
                  </Select>
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon"
                    className="size-8 shrink-0"
                    onClick={() => remove(index)}
                  >
                    <XIcon className="size-4" />
                  </Button>
                </div>
              ))}
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => setIsCreateDialogOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit" disabled={createInvitationIsPending}>
                {createInvitationIsPending ? "Sending..." : "Send invitation"}
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}

export function OrgMembersTable() {
  const [selectedMember, setSelectedMember] = useState<OrgMemberRead | null>(
    null
  )
  const [isChangeRoleOpen, setIsChangeRoleOpen] = useState(false)
  const [isAssignWorkspaceOpen, setIsAssignWorkspaceOpen] = useState(false)
  const { canAdministerOrg } = useOrgMembership()
  const { orgMembers, updateOrgMember, deleteOrgMember, revokeInvitation } =
    useOrgMembers()

  const handleChangeRole = async (role: UserRole) => {
    try {
      if (selectedMember?.user_id) {
        if (selectedMember.role === role) {
          toast({
            title: "Update skipped",
            description: `User ${selectedMember.email} is already a ${role} member`,
          })
          return
        }
        await updateOrgMember({
          userId: selectedMember.user_id,
          requestBody: { role },
        })
      }
    } catch (error) {
      console.error("Failed to change role", error)
    } finally {
      setIsChangeRoleOpen(false)
      setSelectedMember(null)
    }
  }

  const handleRemoveMember = async () => {
    if (selectedMember?.user_id) {
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

  const handleRevokeInvitation = async () => {
    if (selectedMember?.invitation_id) {
      try {
        await revokeInvitation(selectedMember.invitation_id)
      } catch {
        // Error handled in hook
      } finally {
        setSelectedMember(null)
      }
    }
  }

  const toolbarProps: DataTableToolbarProps<OrgMemberRead> = {
    ...defaultToolbarProps,
    actions: <InviteMemberDialogButton />,
  }

  return (
    <div className="space-y-4">
      <Dialog
        open={isChangeRoleOpen}
        onOpenChange={(open) => {
          setIsChangeRoleOpen(open)
          if (!open) setSelectedMember(null)
        }}
      >
        <AlertDialog
          onOpenChange={(isOpen) => {
            if (!isOpen) {
              setSelectedMember(null)
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
                accessorKey: "role",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Role"
                  />
                ),
                cell: ({ row }) => (
                  <div className="text-xs capitalize">
                    {row.getValue<OrgMemberRead["role"]>("role")}
                  </div>
                ),
                enableSorting: true,
                enableHiding: false,
              },
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
                        <Button variant="ghost" className="size-8 p-0">
                          <span className="sr-only">Open menu</span>
                          <DotsHorizontalIcon className="size-4" />
                        </Button>
                      </DropdownMenuTrigger>
                      <DropdownMenuContent align="end">
                        {isInvited ? (
                          <>
                            {canAdministerOrg && (
                              <>
                                <DropdownMenuItem
                                  onSelect={async () => {
                                    if (!member.invitation_id) return
                                    try {
                                      const { token } =
                                        await organizationGetInvitationToken({
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
                            {canAdministerOrg && (
                              <DropdownMenuGroup>
                                <DialogTrigger asChild>
                                  <DropdownMenuItem
                                    onClick={() => {
                                      setSelectedMember(member)
                                      setIsChangeRoleOpen(true)
                                    }}
                                  >
                                    Change role
                                  </DropdownMenuItem>
                                </DialogTrigger>
                                <DropdownMenuItem
                                  onClick={() => {
                                    setSelectedMember(member)
                                    setIsAssignWorkspaceOpen(true)
                                  }}
                                >
                                  Assign to workspace
                                </DropdownMenuItem>
                                <DropdownMenuSeparator />
                                <AlertDialogTrigger asChild>
                                  <DropdownMenuItem
                                    className="text-rose-500 focus:text-rose-600"
                                    onClick={() => setSelectedMember(member)}
                                  >
                                    Remove from organization
                                  </DropdownMenuItem>
                                </AlertDialogTrigger>
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
                  : "Are you sure you want to remove this user from the organization? This action cannot be undone."}
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>Cancel</AlertDialogCancel>
              <AlertDialogAction
                variant="destructive"
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
        <ChangeUserRoleDialog
          selectedUser={selectedMember}
          setOpen={setIsChangeRoleOpen}
          onConfirm={handleChangeRole}
        />
      </Dialog>
      <AssignToWorkspaceDialog
        open={isAssignWorkspaceOpen}
        onOpenChange={(open) => {
          setIsAssignWorkspaceOpen(open)
          if (!open) setSelectedMember(null)
        }}
        member={selectedMember}
      />
    </div>
  )
}

function AssignToWorkspaceDialog({
  open,
  onOpenChange,
  member,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
  member: OrgMemberRead | null
}) {
  const [workspaceId, setWorkspaceId] = useState<string>("")
  const [wsRole, setWsRole] = useState<WorkspaceRole>("editor")
  const [isPending, setIsPending] = useState(false)
  const { workspaces } = useWorkspaceManager()

  const handleSubmit = useCallback(async () => {
    if (!member?.email || !workspaceId) return
    setIsPending(true)
    try {
      await workspacesCreateWorkspaceInvitation({
        workspaceId,
        requestBody: { email: member.email, role: wsRole },
      })
      toast({
        title: "Workspace invitation sent",
        description: `${member.email} has been invited to the workspace.`,
      })
      onOpenChange(false)
    } catch {
      toast({
        title: "Failed to assign workspace",
        description:
          "An error occurred while creating the workspace invitation.",
        variant: "destructive",
      })
    } finally {
      setIsPending(false)
    }
  }, [member?.email, workspaceId, wsRole, onOpenChange])

  // Reset state when dialog opens
  useEffect(() => {
    if (open) {
      setWorkspaceId("")
      setWsRole("editor")
    }
  }, [open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Assign to workspace</DialogTitle>
          <DialogDescription>
            Invite {member?.email} to a workspace.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-4">
          <div className="space-y-2">
            <Label className="text-sm">Workspace</Label>
            <Select value={workspaceId} onValueChange={setWorkspaceId}>
              <SelectTrigger>
                <SelectValue placeholder="Select workspace" />
              </SelectTrigger>
              <SelectContent>
                {(workspaces ?? []).map((ws) => (
                  <SelectItem key={ws.id} value={ws.id}>
                    {ws.name}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label className="text-sm">Workspace role</Label>
            <Select
              value={wsRole}
              onValueChange={(v) => setWsRole(v as WorkspaceRole)}
            >
              <SelectTrigger>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="viewer">Viewer</SelectItem>
                <SelectItem value="editor">Editor</SelectItem>
                <SelectItem value="admin">Admin</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button onClick={handleSubmit} disabled={!workspaceId || isPending}>
            {isPending ? "Sending..." : "Assign"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

function ChangeUserRoleDialog({
  selectedUser,
  setOpen,
  onConfirm,
}: {
  selectedUser: OrgMemberRead | null
  setOpen: (open: boolean) => void
  onConfirm: (role: UserRole) => void
}) {
  const [newRole, setNewRole] = useState<UserRole>("basic")
  const [wsRoleChanges, setWsRoleChanges] = useState<
    Record<string, WorkspaceRole>
  >({})
  const [isSaving, setIsSaving] = useState(false)
  const { workspaceMemberships, workspaceMembershipsLoading } =
    useOrgMemberWorkspaces(selectedUser?.user_id)

  // Reset local state when the selected user changes
  useEffect(() => {
    setWsRoleChanges({})
  }, [selectedUser?.user_id])

  const handleConfirm = useCallback(async () => {
    if (!selectedUser?.user_id) return
    setIsSaving(true)
    try {
      // Update org role
      onConfirm(newRole)

      // Update changed workspace roles
      const updates = Object.entries(wsRoleChanges)
      for (const [wsId, role] of updates) {
        const original = workspaceMemberships?.find(
          (m) => m.workspace_id === wsId
        )
        if (original && original.role !== role) {
          await workspacesUpdateWorkspaceMembership({
            workspaceId: wsId,
            userId: selectedUser.user_id,
            requestBody: { role },
          })
        }
      }
    } catch {
      toast({
        title: "Failed to update roles",
        description: "Some role updates may not have been applied.",
        variant: "destructive",
      })
    } finally {
      setIsSaving(false)
    }
  }, [selectedUser, newRole, wsRoleChanges, workspaceMemberships, onConfirm])

  return (
    <DialogContent>
      <DialogHeader>
        <DialogTitle>Change user role</DialogTitle>
        <DialogDescription>
          Update roles for {selectedUser?.email}
        </DialogDescription>
      </DialogHeader>
      <div className="space-y-4">
        <div className="space-y-2">
          <Label className="text-sm font-medium">Organization role</Label>
          <Select
            value={newRole}
            onValueChange={(value) => setNewRole(value as UserRole)}
          >
            <SelectTrigger>
              <SelectValue placeholder="Select a role" />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value="admin">Admin</SelectItem>
              <SelectItem value="basic">Basic</SelectItem>
            </SelectContent>
          </Select>
        </div>
        {workspaceMembershipsLoading && (
          <p className="text-xs text-muted-foreground">
            Loading workspace memberships...
          </p>
        )}
        {workspaceMemberships && workspaceMemberships.length > 0 && (
          <div className="space-y-3">
            <Label className="text-sm font-medium">Workspace roles</Label>
            {workspaceMemberships.map((membership) => (
              <div
                key={membership.workspace_id}
                className="flex items-center gap-2"
              >
                <span className="flex-1 truncate text-sm">
                  {membership.workspace_name}
                </span>
                <Select
                  value={
                    wsRoleChanges[membership.workspace_id] ?? membership.role
                  }
                  onValueChange={(value) =>
                    setWsRoleChanges((prev) => ({
                      ...prev,
                      [membership.workspace_id]: value as WorkspaceRole,
                    }))
                  }
                >
                  <SelectTrigger className="w-28">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="viewer">Viewer</SelectItem>
                    <SelectItem value="editor">Editor</SelectItem>
                    <SelectItem value="admin">Admin</SelectItem>
                  </SelectContent>
                </Select>
              </div>
            ))}
          </div>
        )}
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={() => setOpen(false)}>
          Cancel
        </Button>
        <Button onClick={handleConfirm} disabled={isSaving}>
          {isSaving ? "Saving..." : "Save changes"}
        </Button>
      </DialogFooter>
    </DialogContent>
  )
}

const defaultToolbarProps: DataTableToolbarProps<OrgMemberRead> = {
  filterProps: {
    placeholder: "Filter by email...",
    column: "email",
  },
}
