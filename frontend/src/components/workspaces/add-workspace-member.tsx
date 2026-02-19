import { zodResolver } from "@hookform/resolvers/zod"
import { Plus } from "lucide-react"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import {
  ApiError,
  type UserRead,
  usersSearchUser,
  type WorkspaceMembershipRead,
  type WorkspaceRead,
} from "@/client"
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
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useAuth } from "@/hooks/use-auth"
import { useOrgMembership } from "@/hooks/use-org-membership"
import {
  useCurrentUserRole,
  useWorkspaceMutations,
} from "@/hooks/use-workspace"
import { WorkspaceRoleEnum } from "@/lib/workspace"

const addUserSchema = z.object({
  email: z.string().email(),
  role: z.enum(WorkspaceRoleEnum).default("editor"),
})
type AddUser = z.infer<typeof addUserSchema>

export function AddWorkspaceMember({
  workspace,
  className,
}: { workspace: WorkspaceRead } & React.HTMLAttributes<HTMLButtonElement>) {
  const { user } = useAuth()
  const { role } = useCurrentUserRole(workspace.id)
  const { canAdministerOrg } = useOrgMembership()
  const { addMember: addWorkspaceMember } = useWorkspaceMutations()
  const [showDialog, setShowDialog] = useState(false)
  const form = useForm<AddUser>({
    resolver: zodResolver(addUserSchema),
    defaultValues: {
      email: "",
      role: "editor",
    },
  })

  const onSubmit = async (values: AddUser) => {
    console.log("SUBMITTING", values)
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
          role: values.role,
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
          disabled={
            !canAdministerOrg &&
            !user?.isPrivileged({ role } as WorkspaceMembershipRead)
          }
          className="h-7 bg-white disabled:cursor-not-allowed"
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
                          {role}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <FormMessage />
                </FormItem>
              )}
            />
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
