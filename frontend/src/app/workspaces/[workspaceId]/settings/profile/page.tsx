"use client"

import { useRouter } from "next/navigation"
import { useAuth } from "@/providers/auth"
import { Input } from "@/components/ui/input"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
} from "@/components/ui/form"
import { useForm } from "react-hook-form"
import { z } from "zod"

const profileSchema = z.object({
  email: z.string().email(),
  firstName: z.string(),
  lastName: z.string()
})

type ProfileFormData = z.infer<typeof profileSchema>

export default function ProfileSettingsPage() {
  const { user } = useAuth()
  const router = useRouter()
  const form = useForm<ProfileFormData>({
    defaultValues: {
      email: user?.email ?? '',
      firstName: user?.first_name ?? '',
      lastName: user?.last_name ?? '',
    }
  })

  if (!user) {
    router.push("/sign-in")
    return null
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Profile</h2>
            <p className="text-md text-muted-foreground">
              View your profile information.
            </p>
          </div>
        </div>

        <Form {...form}>
          <form className="space-y-8">
            <div className="flex w-full flex-col space-y-4">
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem className="flex flex-col max-w-md">
                    <FormLabel>Email</FormLabel>
                    <FormControl>
                      <Input
                        {...field}
                        className="bg-muted"
                        readOnly
                        disabled
                      />
                    </FormControl>
                  </FormItem>
                )}
              />

              <div className="grid grid-cols-2 gap-4 max-w-md">
                <FormField
                  control={form.control}
                  name="firstName"
                  render={({ field }) => (
                    <FormItem className="flex flex-col">
                      <FormLabel>First name</FormLabel>
                      <FormControl>
                        <Input
                          {...field}
                          className="bg-muted"
                          readOnly
                          disabled
                        />
                      </FormControl>
                    </FormItem>
                  )}
                />
                <FormField
                  control={form.control}
                  name="lastName"
                  render={({ field }) => (
                    <FormItem className="flex flex-col">
                      <FormLabel>Last name</FormLabel>
                      <FormControl>
                        <Input
                          {...field}
                          className="bg-muted"
                          readOnly
                          disabled
                        />
                      </FormControl>
                    </FormItem>
                  )}
                />
              </div>
            </div>
          </form>
        </Form>
      </div>
    </div>
  )
}
