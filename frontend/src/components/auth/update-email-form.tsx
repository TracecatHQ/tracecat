"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { UserRead, UserUpdate } from "@/client"
import { Icons } from "@/components/icons"
import { Button } from "@/components/ui/button"
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
import { useUserManager } from "@/lib/hooks"

const updateEmailSchema = z.object({
  email: z.string().email("Invalid email address"),
})
type UpdateEmail = z.infer<typeof updateEmailSchema>

export function UpdateEmailForm({ user }: { user: UserRead }) {
  const { updateCurrentUser, updateCurrentUserPending } = useUserManager()
  const methods = useForm<UpdateEmail>({
    resolver: zodResolver(updateEmailSchema),
    defaultValues: {
      email: "",
    },
  })
  console.log("Pending", updateCurrentUserPending)
  const onSubmit = async (values: UpdateEmail) => {
    if (user.email === values.email) {
      toast({
        title: "Email already set",
        description: `Your email is already ${values.email}`,
      })
      return
    }
    try {
      await updateCurrentUser({
        email: values.email,
      } as UserUpdate)
      console.log("Updating email", values)
      toast({
        title: "Email updated",
        description: `Your account email is now ${values.email}`,
      })
    } catch (error) {
      console.error("Error updating email", error)
    }
  }
  return (
    <Form {...methods}>
      <form onSubmit={methods.handleSubmit(onSubmit)} className="space-y-8">
        <div className="flex w-full flex-col space-y-4">
          <div className="grid gap-4">
            <div className="grid gap-2">
              <FormField
                control={methods.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-xs">Email</FormLabel>
                    <FormControl>
                      <Input placeholder={user.email} {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
          </div>
        </div>
        <Button
          className="text-sm font-semibold"
          disabled={updateCurrentUserPending}
          type="submit"
        >
          {updateCurrentUserPending && (
            <Icons.spinner className="mr-2 size-4 animate-spin" />
          )}
          Update Email
        </Button>
      </form>
    </Form>
  )
}
