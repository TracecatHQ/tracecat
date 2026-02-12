import { zodResolver } from "@hookform/resolvers/zod"
import { CheckCircle2, Copy, Plus } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  type ApiError,
  type WorkspaceMembershipRead,
  type WorkspaceRead,
  workspacesGetInvitationToken,
} from "@/client"
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
import { toast } from "@/components/ui/use-toast"
import { useAuth } from "@/hooks/use-auth"
import { useCurrentUserRole } from "@/hooks/use-workspace"
import { useWorkspaceInvitations } from "@/lib/hooks"
import { WorkspaceRoleEnum } from "@/lib/workspace"

const addUserSchema = z.object({
  email: z.string().email(),
  role: z.enum(WorkspaceRoleEnum).default("editor"),
})
type AddUser = z.infer<typeof addUserSchema>

type DialogState =
  | { type: "form" }
  | { type: "success-invited"; email: string; inviteLink: string }

export function AddWorkspaceMember({
  workspace,
  className,
}: { workspace: WorkspaceRead } & React.HTMLAttributes<HTMLButtonElement>) {
  const { user } = useAuth()
  const { role } = useCurrentUserRole(workspace.id)
  const { createInvitation } = useWorkspaceInvitations(workspace.id)
  const [showDialog, setShowDialog] = useState(false)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [dialogState, setDialogState] = useState<DialogState>({ type: "form" })
  const [copied, setCopied] = useState(false)

  const form = useForm<AddUser>({
    resolver: zodResolver(addUserSchema),
    defaultValues: {
      email: "",
      role: "editor",
    },
  })

  const resetDialog = () => {
    setDialogState({ type: "form" })
    form.reset()
    setCopied(false)
  }

  const handleOpenChange = (open: boolean) => {
    setShowDialog(open)
    if (!open) {
      resetDialog()
    }
  }

  const copyToClipboard = async (text: string) => {
    await navigator.clipboard.writeText(text)
    setCopied(true)
    toast({
      title: "Copied",
      description: "Invitation link copied to clipboard",
    })
    setTimeout(() => setCopied(false), 2000)
  }

  const onSubmit = async (values: AddUser) => {
    setIsSubmitting(true)
    try {
      // Always use the invitation flow
      const invitation = await createInvitation({
        email: values.email,
        role: values.role,
      })

      // Get the invitation token to build the magic link
      const { token } = await workspacesGetInvitationToken({
        workspaceId: workspace.id,
        invitationId: invitation.id,
      })
      const inviteLink = `${window.location.origin}/invitations/workspace/accept?token=${token}`

      setDialogState({
        type: "success-invited",
        email: values.email,
        inviteLink,
      })
    } catch (e) {
      console.error("Error adding user to workspace", e)
      const apiError = e as ApiError
      form.setError("email", {
        message: apiError.message || "An error occurred",
      })
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Dialog open={showDialog} onOpenChange={handleOpenChange}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          disabled={!user?.isPrivileged({ role } as WorkspaceMembershipRead)}
          className="h-7 bg-white disabled:cursor-not-allowed"
        >
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add member
        </Button>
      </DialogTrigger>
      <DialogContent className={className}>
        {dialogState.type === "form" && (
          <>
            <DialogHeader>
              <DialogTitle>Add a workspace member</DialogTitle>
              <DialogDescription>
                Add a user to <b>{workspace.name}</b>. If the user hasn&apos;t
                signed up yet, they&apos;ll receive an invitation and be added
                automatically when they sign in.
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
                  key="role"
                  control={form.control}
                  name="role"
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
                          {WorkspaceRoleEnum.map((role) => (
                            <SelectItem key={role} value={role}>
                              <span className="capitalize">{role}</span>
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
                    disabled={isSubmitting}
                  >
                    {isSubmitting ? "Adding..." : "Add member"}
                  </Button>
                </DialogFooter>
              </form>
            </Form>
          </>
        )}

        {dialogState.type === "success-invited" && (
          <>
            <DialogHeader>
              <DialogTitle className="flex items-center gap-2">
                <CheckCircle2 className="size-5 text-green-600" />
                Invitation created
              </DialogTitle>
              <DialogDescription>
                An invitation has been created for <b>{dialogState.email}</b>.
                Share this link with them to join <b>{workspace.name}</b>.
              </DialogDescription>
            </DialogHeader>
            <div className="space-y-2">
              <label className="text-sm font-medium">Invitation link</label>
              <div className="flex gap-2">
                <Input
                  readOnly
                  value={dialogState.inviteLink}
                  className="text-xs font-mono"
                />
                <Button
                  variant="outline"
                  size="icon"
                  onClick={() => copyToClipboard(dialogState.inviteLink)}
                >
                  {copied ? (
                    <CheckCircle2 className="size-4 text-green-600" />
                  ) : (
                    <Copy className="size-4" />
                  )}
                </Button>
              </div>
              <p className="text-xs text-muted-foreground">
                The user will be added automatically when they sign in with this
                email.
              </p>
            </div>
            <DialogFooter>
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
