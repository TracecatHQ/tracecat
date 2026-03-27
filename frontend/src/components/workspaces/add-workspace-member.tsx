import { zodResolver } from "@hookform/resolvers/zod"
import { Check, Copy, Mail, Plus, Users } from "lucide-react"
import { useMemo, useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { WorkspaceRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { MultiTagCommandInput, type Suggestion } from "@/components/tags-input"
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
  Form,
  FormControl,
  FormDescription,
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
import {
  buildInvitationAcceptUrl,
  getInvitationToken,
  useInvitations,
} from "@/hooks/use-invitations"
import {
  useWorkspaceMembers,
  useWorkspaceMutations,
} from "@/hooks/use-workspace"
import { useOrgMembers, useRbacRoles } from "@/lib/hooks"

const emailInviteSchema = z.object({
  email: z.string().trim().email("Enter a valid email"),
  role_id: z.string().uuid("Please select a role"),
})

const orgUsersInviteSchema = z.object({
  selected_user_ids: z
    .array(z.string().uuid())
    .min(1, "Select at least one organization user"),
  role_id: z.string().uuid("Please select a role"),
})

type EmailInviteForm = z.infer<typeof emailInviteSchema>
type OrgUsersInviteForm = z.infer<typeof orgUsersInviteSchema>
type DialogState =
  | "form"
  | "success_membership"
  | "success_invitation"
  | "success_bulk_membership"
type InviteMode = "email" | "org_users"

export function AddWorkspaceMember({
  workspace,
  className,
}: { workspace: WorkspaceRead } & React.HTMLAttributes<HTMLButtonElement>) {
  const canInviteMembers = useScopeCheck("workspace:member:invite")
  const { createInvitation } = useInvitations({ workspaceId: workspace.id })
  const { addMembersBulk, addMembersBulkPending } = useWorkspaceMutations()
  const { members } = useWorkspaceMembers(workspace.id, {
    enabled: canInviteMembers,
  })
  const { orgMembers } = useOrgMembers()
  const [showDialog, setShowDialog] = useState(false)
  const [dialogState, setDialogState] = useState<DialogState>("form")
  const [inviteMode, setInviteMode] = useState<InviteMode>("email")
  const [inviteLink, setInviteLink] = useState("")
  const [addedEmail, setAddedEmail] = useState("")
  const [addedCount, setAddedCount] = useState(0)
  const [copied, setCopied] = useState(false)
  const { roles } = useRbacRoles({ enabled: showDialog })

  const workspaceRoles = roles.filter(
    (role) => !role.slug || role.slug.startsWith("workspace-")
  )

  const emailForm = useForm<EmailInviteForm>({
    resolver: zodResolver(emailInviteSchema),
    defaultValues: {
      email: "",
      role_id: "",
    },
  })
  const orgUsersForm = useForm<OrgUsersInviteForm>({
    resolver: zodResolver(orgUsersInviteSchema),
    defaultValues: {
      selected_user_ids: [],
      role_id: "",
    },
  })

  const workspaceMemberIds = useMemo(
    () => new Set((members ?? []).map((member) => member.user_id)),
    [members]
  )
  const orgUserSuggestions = useMemo<Suggestion[]>(() => {
    return (orgMembers ?? []).flatMap((member) => {
      if (
        member.status === "invited" ||
        !member.user_id ||
        workspaceMemberIds.has(member.user_id)
      ) {
        return []
      }

      const fullName = [member.first_name, member.last_name]
        .filter(Boolean)
        .join(" ")
      const descriptionParts = [member.email]
      if (member.status === "inactive") {
        descriptionParts.push("Inactive")
      }

      return [
        {
          id: member.user_id,
          label: fullName || member.email,
          value: member.user_id,
          description: descriptionParts.join(" • "),
        },
      ]
    })
  }, [orgMembers, workspaceMemberIds])

  function setRoleId(roleId: string) {
    emailForm.setValue("role_id", roleId, { shouldValidate: true })
    orgUsersForm.setValue("role_id", roleId, { shouldValidate: true })
  }

  function resetDialog() {
    setDialogState("form")
    setInviteMode("email")
    setInviteLink("")
    setAddedEmail("")
    setAddedCount(0)
    setCopied(false)
    emailForm.reset()
    orgUsersForm.reset()
  }

  function handleOpenChange(open: boolean) {
    setShowDialog(open)
    if (!open) {
      resetDialog()
    }
  }

  function handleModeChange(mode: InviteMode) {
    setInviteMode(mode)
    emailForm.clearErrors()
    orgUsersForm.clearErrors()
  }

  async function onSubmitEmail(values: EmailInviteForm) {
    try {
      const result = await createInvitation({
        email: values.email,
        role_id: values.role_id,
        workspace_id: workspace.id,
      })

      setAddedEmail(values.email)

      if (!result.invitation?.id) {
        setDialogState("success_membership")
        return
      }

      const token =
        result.invitation.token?.trim() ||
        (await getInvitationToken(result.invitation.id))
      setInviteLink(
        `${window.location.origin}${buildInvitationAcceptUrl(token)}`
      )
      setDialogState("success_invitation")
    } catch (error) {
      emailForm.setError("email", {
        message:
          error instanceof Error ? error.message : "Failed to add member",
      })
    }
  }

  async function onSubmitOrgUsers(values: OrgUsersInviteForm) {
    try {
      const result = await addMembersBulk({
        user_ids: values.selected_user_ids,
        role_id: values.role_id,
      })
      setAddedCount(result.processed_count)
      setDialogState("success_bulk_membership")
    } catch (error) {
      orgUsersForm.setError("selected_user_ids", {
        message:
          error instanceof Error ? error.message : "Failed to add members",
      })
    }
  }

  async function handleCopy() {
    await navigator.clipboard.writeText(inviteLink)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

  const selectedRoleId =
    inviteMode === "email"
      ? emailForm.watch("role_id")
      : orgUsersForm.watch("role_id")
  const bulkMemberLabel = addedCount === 1 ? "member" : "members"

  return (
    <Dialog open={showDialog} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          disabled={!canInviteMembers}
          className="h-7 bg-white disabled:cursor-not-allowed"
        >
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add member
        </Button>
      </DialogTrigger>
      <DialogContent className={className}>
        {dialogState === "form" ? (
          <>
            <DialogHeader>
              <DialogTitle>Add a workspace member</DialogTitle>
              <DialogDescription>
                Add a member to <b>{workspace.name}</b>. Invite by email or add
                existing organization users directly.
              </DialogDescription>
            </DialogHeader>
            <div className="grid grid-cols-2 gap-2 rounded-md border p-1">
              <Button
                type="button"
                variant={inviteMode === "email" ? "secondary" : "ghost"}
                className="justify-center"
                onClick={() => handleModeChange("email")}
              >
                <Mail className="mr-2 size-4" />
                Email
              </Button>
              <Button
                type="button"
                variant={inviteMode === "org_users" ? "secondary" : "ghost"}
                className="justify-center"
                onClick={() => handleModeChange("org_users")}
              >
                <Users className="mr-2 size-4" />
                Org users
              </Button>
            </div>

            {inviteMode === "email" ? (
              <Form {...emailForm}>
                <form
                  onSubmit={emailForm.handleSubmit(onSubmitEmail)}
                  className="space-y-4"
                >
                  <FormField
                    control={emailForm.control}
                    name="email"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel className="text-sm">Email</FormLabel>
                        <FormControl>
                          <Input
                            {...field}
                            className="text-sm"
                            placeholder="user@example.com"
                          />
                        </FormControl>
                        <FormDescription>
                          Existing organization members are added directly.
                          Everyone else gets an invitation link.
                        </FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={emailForm.control}
                    name="role_id"
                    render={() => (
                      <FormItem>
                        <FormLabel className="text-sm">Role</FormLabel>
                        <Select
                          onValueChange={setRoleId}
                          value={selectedRoleId}
                        >
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue placeholder="Select a role" />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {workspaceRoles.map((role) => (
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
                      type="submit"
                      disabled={emailForm.formState.isSubmitting}
                    >
                      {emailForm.formState.isSubmitting
                        ? "Adding..."
                        : "Add member"}
                    </Button>
                  </DialogFooter>
                </form>
              </Form>
            ) : (
              <Form {...orgUsersForm}>
                <form
                  onSubmit={orgUsersForm.handleSubmit(onSubmitOrgUsers)}
                  className="space-y-4"
                >
                  <FormField
                    control={orgUsersForm.control}
                    name="selected_user_ids"
                    render={({ field }) => (
                      <FormItem>
                        <FormLabel className="text-sm">
                          Organization users
                        </FormLabel>
                        <FormControl>
                          <MultiTagCommandInput
                            value={field.value}
                            onChange={field.onChange}
                            suggestions={orgUserSuggestions}
                            searchKeys={["label", "value", "description"]}
                            placeholder="Search organization users"
                            disabled={orgUserSuggestions.length === 0}
                          />
                        </FormControl>
                        <FormDescription>
                          {orgUserSuggestions.length === 0
                            ? "No eligible organization users are available. Members already in this workspace are excluded."
                            : "Select one or more organization users to add directly. Members already in this workspace are excluded."}
                        </FormDescription>
                        <FormMessage />
                      </FormItem>
                    )}
                  />
                  <FormField
                    control={orgUsersForm.control}
                    name="role_id"
                    render={() => (
                      <FormItem>
                        <FormLabel className="text-sm">Role</FormLabel>
                        <Select
                          onValueChange={setRoleId}
                          value={selectedRoleId}
                        >
                          <FormControl>
                            <SelectTrigger>
                              <SelectValue placeholder="Select a role" />
                            </SelectTrigger>
                          </FormControl>
                          <SelectContent>
                            {workspaceRoles.map((role) => (
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
                      type="submit"
                      disabled={
                        orgUsersForm.formState.isSubmitting ||
                        addMembersBulkPending ||
                        orgUserSuggestions.length === 0
                      }
                    >
                      {orgUsersForm.formState.isSubmitting ||
                      addMembersBulkPending
                        ? "Adding..."
                        : "Add members"}
                    </Button>
                  </DialogFooter>
                </form>
              </Form>
            )}
          </>
        ) : dialogState === "success_membership" ? (
          <>
            <DialogHeader>
              <DialogTitle>Access applied</DialogTitle>
              <DialogDescription>
                Access for <b>{addedEmail}</b> has been updated. No invitation
                link was required.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter className="gap-2">
              <Button variant="outline" onClick={resetDialog}>
                Add another
              </Button>
              <Button onClick={() => handleOpenChange(false)}>Done</Button>
            </DialogFooter>
          </>
        ) : dialogState === "success_bulk_membership" ? (
          <>
            <DialogHeader>
              <DialogTitle>Access applied</DialogTitle>
              <DialogDescription>
                Access was applied to <b>{addedCount}</b> organization{" "}
                {bulkMemberLabel}. No invitation links were required.
              </DialogDescription>
            </DialogHeader>
            <DialogFooter className="gap-2">
              <Button variant="outline" onClick={resetDialog}>
                Add another
              </Button>
              <Button onClick={() => handleOpenChange(false)}>Done</Button>
            </DialogFooter>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>Invitation created</DialogTitle>
              <DialogDescription>
                <b>{addedEmail}</b> is not yet an organization member. Share
                this link so they can join the workspace.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <Input
                  readOnly
                  value={inviteLink}
                  className="font-mono text-sm"
                />
                <Button
                  variant="outline"
                  size="icon"
                  className="shrink-0"
                  onClick={handleCopy}
                >
                  {copied ? (
                    <Check className="h-4 w-4" />
                  ) : (
                    <Copy className="h-4 w-4" />
                  )}
                </Button>
              </div>
            </div>
            <DialogFooter className="gap-2">
              <Button variant="outline" onClick={resetDialog}>
                Add another
              </Button>
              <Button onClick={() => handleOpenChange(false)}>Done</Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
