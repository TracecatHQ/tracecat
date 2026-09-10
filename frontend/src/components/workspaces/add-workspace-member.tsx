import { zodResolver } from "@hookform/resolvers/zod"
import { Plus } from "lucide-react"
import Link from "next/link"
import { useState } from "react"
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
import { useWorkspaceMutations } from "@/hooks/use-workspace"

const addUserSchema = z.object({
  email: z.string().email(),
})
type AddUser = z.infer<typeof addUserSchema>

export function AddWorkspaceMember({
  workspace,
  className,
}: { workspace: WorkspaceRead } & React.HTMLAttributes<HTMLButtonElement>) {
  const canInviteMembers = useScopeCheck("workspace:member:invite")
  const canInviteOrgMembers = useScopeCheck("org:member:invite")
  // The org dialog this link opens renders nothing without org:member:invite.
  const canHandOffToOrgInvite =
    canInviteMembers === true && canInviteOrgMembers === true
  const { addMember: addWorkspaceMember } = useWorkspaceMutations()
  const [showDialog, setShowDialog] = useState(false)

  const form = useForm<AddUser>({
    resolver: zodResolver(addUserSchema),
    defaultValues: {
      email: "",
    },
  })

  const onSubmit = async (values: AddUser) => {
    let userToAdd: UserRead
    try {
      // Check if the user exists
      userToAdd = await usersSearchUser({
        email: values.email,
        workspaceId: workspace.id,
      })
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        form.setError("email", {
          message:
            "Couldn't find a user with this email. Please ensure they have signed up via email/password, OAuth2.0, or SSO.",
        })
      } else {
        console.error("Unexpected error", e)
        form.setError("email", {
          message: `Error adding user to workspace: ${(e as ApiError).message}`,
        })
      }
      return
    }

    // We've found this user
    try {
      await addWorkspaceMember({
        workspaceId: workspace.id,
        requestBody: {
          user_id: userToAdd.id,
        },
      })
      setShowDialog(false)
    } catch (e) {
      console.error("Error adding user to workspace", e)
      form.setError("email", {
        message: `Error adding user to workspace: ${(e as ApiError).message}`,
      })
    }
  }

  return (
    <Dialog open={showDialog} onOpenChange={setShowDialog}>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          disabled={!canInviteMembers}
          className="h-7 bg-background disabled:cursor-not-allowed"
        >
          <Plus className="mr-1 h-3.5 w-3.5" />
          Add member
        </Button>
      </DialogTrigger>
      <DialogContent className={className}>
        <DialogHeader>
          <DialogTitle>Add a workspace member</DialogTitle>
          <div className="flex text-sm leading-relaxed text-muted-foreground">
            <span>
              Add a user to the <b className="inline-block">{workspace.name}</b>{" "}
              workspace.
            </span>
          </div>
        </DialogHeader>
        <Form {...form}>
          <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-4">
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
                      placeholder="test@domain.com"
                    />
                  </FormControl>
                  <FormMessage />
                </FormItem>
              )}
            />
            {canHandOffToOrgInvite && (
              <p className="text-sm text-muted-foreground">
                Not signed up yet?{" "}
                <Link
                  href={`/organization/members?inviteWorkspace=${encodeURIComponent(workspace.id)}`}
                  className="underline underline-offset-4 hover:text-foreground"
                >
                  Invite them to the organization
                </Link>{" "}
                with a role on this workspace.
              </p>
            )}
            <DialogFooter>
              <Button type="submit" variant="default">
                Add member
              </Button>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
