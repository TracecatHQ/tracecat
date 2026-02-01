"use client"

import { zodResolver } from "@hookform/resolvers/zod"
import { useState } from "react"
import { useForm } from "react-hook-form"
import { z } from "zod"
import type { UserUpdate } from "@/client"
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
import { Switch } from "@/components/ui/switch"
import { toast } from "@/components/ui/use-toast"
import { useUserManager } from "@/lib/hooks"

const resetPasswordSchema = z
  .object({
    password: z.string().min(8, "Password must be at least 8 characters"),
    confirmPassword: z
      .string()
      .min(8, "Password must be at least 8 characters"),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Passwords do not match",
    path: ["confirmPassword"],
  })

type UpdatePassword = z.infer<typeof resetPasswordSchema>

export function UpdatePasswordForm() {
  const [showPassword, setShowPassword] = useState(false)
  const { updateCurrentUser, updateCurrentUserPending } = useUserManager()
  const methods = useForm<UpdatePassword>({
    resolver: zodResolver(resetPasswordSchema),
    defaultValues: {
      password: "",
      confirmPassword: "",
    },
  })
  const onSubmit = async (values: UpdatePassword) => {
    try {
      await updateCurrentUser({
        password: values.password,
      } as UserUpdate)
      toast({
        title: "Password updated",
        description: `Your password has been updated.`,
      })
    } catch (error) {
      console.error("Error updating password", error)
    }
  }
  return (
    <Form {...methods}>
      <form onSubmit={methods.handleSubmit(onSubmit)} className="space-y-8">
        <div className="flex w-full flex-col space-y-4">
          <div className="grid gap-4">
            <div className="flex items-center space-x-4">
              <Switch
                checked={showPassword}
                onCheckedChange={setShowPassword}
              />
              <span className="text-xs text-muted-foreground">
                {showPassword ? "Hide" : "Show"} password
              </span>
            </div>
            <div className="grid gap-2">
              <FormField
                control={methods.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-sm font-medium">
                      New password
                    </FormLabel>
                    <FormControl>
                      <Input
                        type={showPassword ? "text" : "password"}
                        placeholder="••••••••••••••••"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <div className="grid gap-2">
              <FormField
                control={methods.control}
                name="confirmPassword"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-sm font-medium">
                      Confirm password
                    </FormLabel>
                    <FormControl>
                      <Input
                        type={showPassword ? "text" : "password"}
                        placeholder="••••••••••••••••"
                        {...field}
                      />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
          </div>
        </div>
        <Button
          className="rounded-md bg-black px-4 py-2 text-sm text-white hover:bg-black/90"
          disabled={updateCurrentUserPending}
          type="submit"
        >
          {updateCurrentUserPending && (
            <Icons.spinner className="mr-2 size-4 animate-spin" />
          )}
          Update password
        </Button>
      </form>
    </Form>
  )
}
