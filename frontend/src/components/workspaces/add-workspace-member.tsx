import { ApiError, UserRead, usersSearchUser, WorkspaceRead } from "@/client"
import { useAuth } from "@/providers/auth"
import { useWorkspace } from "@/providers/workspace"
import { zodResolver } from "@hookform/resolvers/zod"
import { CirclePlusIcon } from "lucide-react"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
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
import { toast } from "@/components/ui/use-toast"

const addUserSchema = z.object({
  email: z.string().email(),
})
type AddUser = z.infer<typeof addUserSchema>

export function AddWorkspaceMember({
  workspace,
  className,
}: { workspace: WorkspaceRead } & React.HTMLAttributes<HTMLButtonElement>) {
  const { user } = useAuth()
  const { addWorkspaceMember } = useWorkspace()
  const methods = useForm<AddUser>({
    resolver: zodResolver(addUserSchema),
    defaultValues: {
      email: "",
    },
  })

  const onSubmit = async (values: AddUser) => {
    console.log("SUBMITTING", values)
    let userToAdd: UserRead
    try {
      // Check if the user exists
      userToAdd = await usersSearchUser({
        email: values.email,
      })
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        console.error("Couldn't find user with this email.", e.message)
      } else {
        console.error("Unexpected error", e)
      }
      toast({
        title: "Couldn't find a user with this email. ",
        description:
          "Users must first sign-up via email/password or OAuth2.0 before getting invited to a workspace.",
      })
      return
    }

    // We've found this user
    try {
      await addWorkspaceMember(userToAdd.id)
    } catch (e) {
      console.error("Error adding user to workspace", e)
    }
  }

  const userIsAdmin = user?.is_superuser || user?.role === "admin"
  return (
    <Dialog>
      <DialogTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          disabled={!userIsAdmin}
          className="disabled:cursor-not-allowed"
        >
          <CirclePlusIcon className="mr-2 size-4" />
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
        <Form {...methods}>
          <form onSubmit={methods.handleSubmit(onSubmit)}>
            <FormField
              key="email"
              control={methods.control}
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
            <DialogFooter>
              <DialogClose asChild>
                <Button type="submit" variant="default">
                  Add member
                </Button>
              </DialogClose>
            </DialogFooter>
          </form>
        </Form>
      </DialogContent>
    </Dialog>
  )
}
