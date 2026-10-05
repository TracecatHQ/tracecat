"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { Plus } from "lucide-react"
import { useEffect, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  ApiError,
  type UserRead,
  usersSearchUser,
  type WorkspaceRead,
} from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
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
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import {
  useWorkspaceAssignableRoles,
  useWorkspaceMutations,
} from "@/hooks/use-workspace"
import { getApiErrorDetail } from "@/lib/errors"

/** Preselected when the caller may grant it, matching the API default. */
const DEFAULT_WORKSPACE_ROLE_SLUG = "workspace-editor"

const addMemberSchema = z.object({
  email: z.string().email("Invalid email address"),
  role_id: z.string().uuid("Select a role"),
})
type AddMemberValues = z.infer<typeof addMemberSchema>

function isNotFound(error: unknown): boolean {
  return error instanceof ApiError && error.status === 404
}

/**
 * Header button for adding workspace members. Always visible; users without
 * an invite scope get a neutral toast instead of the dialog.
 */
export function AddWorkspaceMember({
  workspace,
}: {
  workspace: WorkspaceRead
}) {
  const canAddWorkspaceMembers = useScopeCheck("workspace:member:invite")
  const canInviteOrgMembers = useScopeCheck("org:member:invite")
  const scopesLoading =
    canAddWorkspaceMembers === undefined || canInviteOrgMembers === undefined
  const canAddMembers =
    canAddWorkspaceMembers === true || canInviteOrgMembers === true
  const [open, setOpen] = useState(false)

  const handleClick = () => {
    if (scopesLoading) {
      return
    }
    if (!canAddMembers) {
      toast({
        title: "Insufficient permissions",
        description:
          "You don't have permission to add members to this workspace. Ask a workspace or organization admin for access.",
      })
      return
    }
    setOpen(true)
  }

  return (
    <>
      <Button
        variant="outline"
        size="sm"
        className="h-7 bg-background"
        onClick={handleClick}
      >
        <Plus className="mr-1 h-3.5 w-3.5" />
        Add member
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="w-[calc(100%-2rem)] max-w-lg">
          {/* Mounted only while open so its queries stay idle when closed. */}
          {open && (
            <AddWorkspaceMemberForm
              workspace={workspace}
              canInviteOrgMembers={canInviteOrgMembers === true}
              onOpenChange={setOpen}
            />
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}

function AddWorkspaceMemberForm({
  workspace,
  canInviteOrgMembers,
  onOpenChange,
}: {
  workspace: WorkspaceRead
  canInviteOrgMembers: boolean
  onOpenChange: (open: boolean) => void
}) {
  const { addMember, inviteMember } = useWorkspaceMutations()
  const { assignableRoles, assignableRolesLoading } =
    useWorkspaceAssignableRoles(workspace.id)
  const form = useForm<AddMemberValues>({
    resolver: zodResolver(addMemberSchema),
    defaultValues: { email: "", role_id: "" },
  })

  useEffect(() => {
    if (!assignableRoles?.length || form.getValues("role_id")) {
      return
    }
    const defaultRole =
      assignableRoles.find(
        (role) => role.slug === DEFAULT_WORKSPACE_ROLE_SLUG
      ) ?? assignableRoles[0]
    form.setValue("role_id", defaultRole.id)
  }, [assignableRoles, form])

  const inviteToOrganization = async (email: string, roleId: string) => {
    await inviteMember({
      email,
      grants: [{ workspace_id: workspace.id, role_id: roleId }],
    })
    toast({
      title: "Invitation sent",
      description: `${email} was invited to the organization with access to ${workspace.name}.`,
    })
    onOpenChange(false)
  }

  const onSubmit = async ({ email, role_id }: AddMemberValues) => {
    try {
      let user: UserRead | null = null
      try {
        user = await usersSearchUser({ email, workspaceId: workspace.id })
      } catch (error) {
        if (!isNotFound(error)) {
          throw error
        }
      }

      if (user) {
        try {
          await addMember({
            workspaceId: workspace.id,
            requestBody: { user_id: user.id, role_id },
          })
          toast({
            title: "Member added",
            description: `${email} was added to ${workspace.name}.`,
          })
          onOpenChange(false)
          return
        } catch (error) {
          // 404 means the account exists but is outside this organization.
          if (!isNotFound(error)) {
            throw error
          }
        }
      }

      if (!canInviteOrgMembers) {
        form.setError("email", {
          message:
            "This user isn't in your organization. Ask an organization admin to invite them.",
        })
        return
      }
      await inviteToOrganization(email, role_id)
    } catch (error) {
      console.error("Failed to add workspace member", error)
      form.setError("email", {
        message:
          getApiErrorDetail(error) ?? "The request could not be completed.",
      })
    }
  }

  const isSubmitting = form.formState.isSubmitting
  const noAssignableRoles =
    !assignableRolesLoading && (assignableRoles?.length ?? 0) === 0
  const submitDisabled =
    isSubmitting || assignableRolesLoading || noAssignableRoles

  return (
    <>
      <DialogHeader>
        <DialogTitle>Add workspace member</DialogTitle>
        <DialogDescription>
          {canInviteOrgMembers
            ? `Add a user to ${workspace.name}. Anyone not yet in the organization is invited to it with this role.`
            : `Add an existing organization member to ${workspace.name}.`}
        </DialogDescription>
      </DialogHeader>
      <Form {...form}>
        <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
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
                <FormMessage />
              </FormItem>
            )}
          />
          <FormField
            control={form.control}
            name="role_id"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Role</FormLabel>
                <Select
                  value={field.value}
                  onValueChange={field.onChange}
                  disabled={assignableRolesLoading || noAssignableRoles}
                >
                  <FormControl>
                    <SelectTrigger aria-label="Role">
                      <SelectValue
                        placeholder={
                          noAssignableRoles
                            ? "No roles you can grant"
                            : "Select a role"
                        }
                      />
                    </SelectTrigger>
                  </FormControl>
                  <SelectContent>
                    {assignableRoles?.map((role) => (
                      <SelectItem key={role.id} value={role.id}>
                        {role.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <FormMessage />
              </FormItem>
            )}
          />
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={submitDisabled}>
              {isSubmitting ? "Adding..." : "Add member"}
            </Button>
          </DialogFooter>
        </form>
      </Form>
    </>
  )
}
