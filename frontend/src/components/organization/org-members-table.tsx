"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { DialogTrigger } from "@radix-ui/react-dialog"
import { DotsHorizontalIcon, PlusIcon } from "@radix-ui/react-icons"
import { FolderIcon, GlobeIcon, Trash2Icon, XIcon } from "lucide-react"
import { useState } from "react"
import { useFieldArray, useForm } from "react-hook-form"
import { z } from "zod"
import type { OrgMemberRead } from "@/client"
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
import { ScrollArea } from "@/components/ui/scroll-area"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { getRelativeTime } from "@/lib/event-history"
import {
  useOrgMembers,
  useRbacRoles,
  useRbacUserAssignments,
  useWorkspaceManager,
} from "@/lib/hooks"
import { toast } from "../ui/use-toast"

const invitationFormSchema = z.object({
  email: z.string().email("Invalid email address"),
  role_id: z.string().uuid("Please select a role"),
  workspace_assignments: z.array(
    z.object({
      workspace_id: z.string().uuid(),
      role_id: z.string().uuid(),
    })
  ),
})

type InvitationFormValues = z.infer<typeof invitationFormSchema>

function InviteMemberDialogButton() {
  const canInviteMembers = useScopeCheck("org:member:invite") === true
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false)
  const { createInvitation, createInvitationIsPending } = useOrgMembers()
  const { roles } = useRbacRoles()
  const { workspaces } = useWorkspaceManager()

  const orgRoles = roles.filter(
    (r) => !r.slug || r.slug.startsWith("organization-")
  )
  const workspaceRoles = roles.filter(
    (r) => !r.slug || r.slug.startsWith("workspace-")
  )

  const form = useForm<InvitationFormValues>({
    resolver: zodResolver(invitationFormSchema),
    defaultValues: {
      email: "",
      role_id: "",
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
        role_id: values.role_id,
        workspace_assignments: values.workspace_assignments,
      })
      form.reset()
      setIsCreateDialogOpen(false)
    } catch {
      // Error handled in hook
    }
  }

  if (!canInviteMembers) {
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
              name="role_id"
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
                      {orgRoles.map((role) => (
                        <SelectItem key={role.id} value={role.id}>
                          {role.name}
                        </SelectItem>
                      ))}
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
                <Label className="text-sm font-medium">Workspaces</Label>
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={availableWorkspaces.length === 0}
                  onClick={() => append({ workspace_id: "", role_id: "" })}
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
                    value={form.watch(`workspace_assignments.${index}.role_id`)}
                    onValueChange={(value) =>
                      form.setValue(
                        `workspace_assignments.${index}.role_id`,
                        value
                      )
                    }
                  >
                    <SelectTrigger className="w-36">
                      <SelectValue placeholder="Role" />
                    </SelectTrigger>
                    <SelectContent>
                      {workspaceRoles.map((role) => (
                        <SelectItem key={role.id} value={role.id}>
                          {role.name}
                        </SelectItem>
                      ))}
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
  const canInviteMembers = useScopeCheck("org:member:invite") === true
  const canRemoveMembers = useScopeCheck("org:member:remove") === true
  const canReadRbac = useScopeCheck("org:rbac:read") === true
  const { orgMembers, deleteOrgMember, revokeInvitation } = useOrgMembers()

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
      <Dialog open={isChangeRoleOpen} onOpenChange={setIsChangeRoleOpen}>
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
                accessorKey: "role_name",
                header: ({ column }) => (
                  <DataTableColumnHeader
                    className="text-xs"
                    column={column}
                    title="Role"
                  />
                ),
                cell: ({ row }) => (
                  <div className="text-xs">
                    {row.getValue<string>("role_name")}
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
                            {canInviteMembers && (
                              <>
                                <DropdownMenuItem
                                  onSelect={async () => {
                                    if (!member.token) return
                                    try {
                                      const url = `${window.location.origin}/invitations/accept?token=${member.token}`
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
                            {(canReadRbac || canRemoveMembers) && (
                              <DropdownMenuGroup>
                                {canReadRbac && (
                                  <DialogTrigger asChild>
                                    <DropdownMenuItem
                                      onClick={() => {
                                        setSelectedMember(member)
                                        setIsChangeRoleOpen(true)
                                      }}
                                    >
                                      Manage roles
                                    </DropdownMenuItem>
                                  </DialogTrigger>
                                )}
                                {canRemoveMembers && (
                                  <>
                                    {canReadRbac && <DropdownMenuSeparator />}
                                    <AlertDialogTrigger asChild>
                                      <DropdownMenuItem
                                        className="text-rose-500 focus:text-rose-600"
                                        onClick={() =>
                                          setSelectedMember(member)
                                        }
                                      >
                                        Remove from organization
                                      </DropdownMenuItem>
                                    </AlertDialogTrigger>
                                  </>
                                )}
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
        {selectedMember && (
          <ManageUserRolesDialog
            member={selectedMember}
            onOpenChange={setIsChangeRoleOpen}
          />
        )}
      </Dialog>
    </div>
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
  const userId = member.user_id ?? undefined

  const {
    userAssignments,
    createUserAssignment,
    createUserAssignmentIsPending,
    deleteUserAssignment,
    deleteUserAssignmentIsPending,
  } = useRbacUserAssignments({ userId })
  const { roles } = useRbacRoles()
  const { workspaces } = useWorkspaceManager()
  const canReadRbac = useScopeCheck("org:rbac:read") === true
  const canCreateAssignment = useScopeCheck("org:rbac:create") === true
  const canDeleteAssignment = useScopeCheck("org:rbac:delete") === true

  const handleAddRole = async () => {
    if (!roleId || !userId) return
    await createUserAssignment({
      user_id: userId,
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
        {canCreateAssignment && (
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
        )}

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
                    {canDeleteAssignment && (
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => handleRemoveRole(assignment.id)}
                        disabled={deleteUserAssignmentIsPending}
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
      </div>
      {!canReadRbac && (
        <p className="text-sm text-muted-foreground">
          You do not have permission to manage RBAC assignments.
        </p>
      )}
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
    placeholder: "Filter by email...",
    column: "email",
  },
}
