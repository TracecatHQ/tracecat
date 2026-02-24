import { zodResolver } from "@hookform/resolvers/zod"
import { Check, Copy, Plus } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  ApiError,
  invitationsGetInvitationToken,
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
import { useInvitations } from "@/hooks/use-invitations"
import { useRbacRoles } from "@/lib/hooks"

const inviteSchema = z.object({
  email: z.string().email(),
  role_id: z.string().min(1, "Please select a role"),
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
        requestBody: {
          email: values.email,
          role_id: values.role_id,
          workspace_id: workspace.id,
        },
      })

      setAddedEmail(values.email)

      if (result === null) {
        // Direct membership — user was already an org member
        setDialogState("success_membership")
      } else {
        // Invitation created — build the invite link
        let token = result.token
        if (!token) {
          const tokenResponse = await invitationsGetInvitationToken({
            invitationId: result.id,
          })
          token = tokenResponse.token
        }
        if (token) {
          setInviteLink(
            `${window.location.origin}/invitations/accept?token=${token}`
          )
        }
        setDialogState("success_invitation")
      }
    } catch (e) {
      if (e instanceof ApiError) {
        form.setError("email", {
          message:
            (e.body as Record<string, unknown>)?.detail?.toString() ??
            e.message,
        })
      } else {
        form.setError("email", {
          message: "Failed to add member",
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
              <DialogTitle>Add a workspace member</DialogTitle>
              <DialogDescription>
                Add a member to the{" "}
                <b className="inline-block">{workspace.name}</b> workspace. Org
                members are added directly; external users receive an invitation
                link.
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
                    {form.formState.isSubmitting ? "Adding..." : "Add member"}
                  </Button>
                </DialogFooter>
              </form>
            </Form>
          </>
        ) : dialogState === "success_membership" ? (
          <>
            <DialogHeader>
              <DialogTitle>Member added</DialogTitle>
              <DialogDescription>
                <b>{addedEmail}</b> has been added to the workspace.
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
                <b>{addedEmail}</b> is not an org member. Share this link with
                them to join the workspace.
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
