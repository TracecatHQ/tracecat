import { zodResolver } from "@hookform/resolvers/zod"
import { Check, Copy, Plus } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { WorkspaceRead } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
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
import { useRbacRoles } from "@/lib/hooks"

const inviteSchema = z.object({
  email: z.string().email(),
  role_id: z.string().uuid("Please select a role"),
})

type InviteForm = z.infer<typeof inviteSchema>
type DialogState = "form" | "success_membership" | "success_invitation"

export function AddWorkspaceMember({
  workspace,
  className,
}: { workspace: WorkspaceRead } & React.HTMLAttributes<HTMLButtonElement>) {
  const canInviteMembers = useScopeCheck("workspace:member:invite")
  const { createInvitation } = useInvitations({ workspaceId: workspace.id })
  const [showDialog, setShowDialog] = useState(false)
  const [dialogState, setDialogState] = useState<DialogState>("form")
  const [inviteLink, setInviteLink] = useState("")
  const [addedEmail, setAddedEmail] = useState("")
  const [copied, setCopied] = useState(false)
  const { roles } = useRbacRoles({ enabled: showDialog })

  const workspaceRoles = roles.filter(
    (role) => !role.slug || role.slug.startsWith("workspace-")
  )

  const form = useForm<InviteForm>({
    resolver: zodResolver(inviteSchema),
    defaultValues: {
      email: "",
      role_id: "",
    },
  })

  function resetDialog() {
    setDialogState("form")
    setInviteLink("")
    setAddedEmail("")
    setCopied(false)
    form.reset()
  }

  function handleOpenChange(open: boolean) {
    setShowDialog(open)
    if (!open) {
      resetDialog()
    }
  }

  async function onSubmit(values: InviteForm) {
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
      form.setError("email", {
        message:
          error instanceof Error ? error.message : "Failed to add member",
      })
    }
  }

  async function handleCopy() {
    await navigator.clipboard.writeText(inviteLink)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }

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
                Add a member to <b>{workspace.name}</b>. Existing organization
                members are added directly. Everyone else gets an invitation
                link.
              </DialogDescription>
            </DialogHeader>
            <Form {...form}>
              <form
                onSubmit={form.handleSubmit(onSubmit)}
                className="space-y-4"
              >
                <FormField
                  control={form.control}
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
                      <FormMessage />
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="role_id"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-sm">Role</FormLabel>
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
                  <Button type="submit" disabled={form.formState.isSubmitting}>
                    {form.formState.isSubmitting ? "Adding..." : "Add member"}
                  </Button>
                </DialogFooter>
              </form>
            </Form>
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
