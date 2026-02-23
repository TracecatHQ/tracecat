import { zodResolver } from "@hookform/resolvers/zod"
import { Check, Copy, Plus } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  ApiError,
  type WorkspaceRead,
  workspacesGetWorkspaceInvitationToken,
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
import { useWorkspaceInvitations } from "@/hooks/use-workspace"
import { useRbacRoles } from "@/lib/hooks"

const inviteSchema = z.object({
  email: z.string().email(),
  role_id: z.string().min(1, "Please select a role"),
})
type InviteForm = z.infer<typeof inviteSchema>

type DialogState = "form" | "success"

export function AddWorkspaceMember({
  workspace,
  className,
}: { workspace: WorkspaceRead } & React.HTMLAttributes<HTMLButtonElement>) {
  const canInviteMembers = useScopeCheck("workspace:member:invite")
  const { createInvitation } = useWorkspaceInvitations(workspace.id)
  const [showDialog, setShowDialog] = useState(false)
  const [dialogState, setDialogState] = useState<DialogState>("form")
  const [inviteLink, setInviteLink] = useState("")
  const [invitedEmail, setInvitedEmail] = useState("")
  const [copied, setCopied] = useState(false)
  const { roles } = useRbacRoles({ enabled: showDialog })

  const workspaceRoles = roles.filter(
    (r) => !r.slug || r.slug.startsWith("workspace-")
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
    setInvitedEmail("")
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
      const invitation = await createInvitation({
        workspaceId: workspace.id,
        requestBody: {
          email: values.email,
          role_id: values.role_id,
        },
      })

      // Get the token — it may already be in the response, or fetch it
      let token = invitation.token
      if (!token) {
        const tokenResponse = await workspacesGetWorkspaceInvitationToken({
          workspaceId: workspace.id,
          invitationId: invitation.id,
        })
        token = tokenResponse.token
      }

      const link = `${window.location.origin}/invitations/workspace/accept?token=${token}`
      setInviteLink(link)
      setInvitedEmail(values.email)
      setDialogState("success")
    } catch (e) {
      if (e instanceof ApiError) {
        form.setError("email", {
          message:
            (e.body as Record<string, unknown>)?.detail?.toString() ??
            e.message,
        })
      } else {
        form.setError("email", {
          message: "Failed to create invitation",
        })
      }
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
              <DialogTitle>Invite a workspace member</DialogTitle>
              <DialogDescription>
                Send an invitation to join the{" "}
                <b className="inline-block">{workspace.name}</b> workspace.
              </DialogDescription>
            </DialogHeader>
            <Form {...form}>
              <form
                onSubmit={form.handleSubmit(onSubmit)}
                className="space-y-4"
              >
                <FormField
                  key="email"
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
                  key="role_id"
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
                  <Button
                    type="submit"
                    variant="default"
                    disabled={form.formState.isSubmitting}
                  >
                    {form.formState.isSubmitting
                      ? "Sending..."
                      : "Send invitation"}
                  </Button>
                </DialogFooter>
              </form>
            </Form>
          </>
        ) : (
          <>
            <DialogHeader>
              <DialogTitle>Invitation sent</DialogTitle>
              <DialogDescription>
                An invitation has been created for <b>{invitedEmail}</b>. Share
                this link with them to join the workspace.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-4">
              <div className="flex items-center gap-2">
                <Input
                  readOnly
                  value={inviteLink}
                  className="text-sm font-mono"
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
                Invite another
              </Button>
              <Button onClick={() => handleOpenChange(false)}>Done</Button>
            </DialogFooter>
          </>
        )}
      </DialogContent>
    </Dialog>
  )
}
