"use client"

import { useState } from "react"
import { UserRead, UserUpdate } from "@/client"
import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { useUserManager } from "@/lib/hooks"
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
import { Icons } from "@/components/icons"

const updateEmailSchema = z.object({
  email: z.string().email("Invalid email address"),
})
type UpdateEmail = z.infer<typeof updateEmailSchema>

export function UpdateEmailForm({ user }: { user: UserRead }) {
  const [isLoading, setIsLoading] = useState(false)
  const { updateCurrentUser } = useUserManager()
  const methods = useForm<UpdateEmail>({
    resolver: zodResolver(updateEmailSchema),
    defaultValues: {
      email: "",
    },
  })
  const onSubmit = async (values: UpdateEmail) => {
    try {
      setIsLoading(true)
      await updateCurrentUser({
        email: values.email,
      } as UserUpdate)
      console.log("Updating email", values)
      toast({
        title: "Email updated",
        description: `Your email has been updated. ${values.email}`,
      })
    } finally {
      setIsLoading(false)
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
          disabled={isLoading}
          type="submit"
        >
          {isLoading && <Icons.spinner className="mr-2 size-4 animate-spin" />}
          Update Email
        </Button>
      </form>
    </Form>
  )
}
