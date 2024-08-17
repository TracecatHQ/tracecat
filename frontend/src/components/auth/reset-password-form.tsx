"use client"

import { useState } from "react"
import { UserRead } from "@/client"
import { zodResolver } from "@hookform/resolvers/zod"
import { useForm } from "react-hook-form"
import { z } from "zod"

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
import { Icons } from "@/components/icons"

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

type ResetPassword = z.infer<typeof resetPasswordSchema>

export function ResetPasswordForm({ user }: { user: UserRead }) {
  const [isLoading, setIsLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)
  const form = useForm<ResetPassword>({
    resolver: zodResolver(resetPasswordSchema),
    defaultValues: {
      password: "",
      confirmPassword: "",
    },
  })
  const onSubmit = (values: ResetPassword) => {
    try {
      setIsLoading(true)
      toast({
        title: "Password updated",
        description: `Your password has been updated. ${values}`,
      })
    } finally {
      setIsLoading(false)
    }
  }
  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
        <div className="flex w-full flex-col space-y-4">
          <div className="grid gap-4">
            <div className="flex items-center space-x-4">
              <Switch
                checked={showPassword}
                onCheckedChange={setShowPassword}
              />
              <span className="text-xs text-muted-foreground">
                {showPassword ? "Show" : "Hide"} password
              </span>
            </div>
            <div className="grid gap-2">
              <FormField
                control={form.control}
                name="password"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-xs">New Password</FormLabel>
                    <FormControl>
                      <Input
                        type={showPassword ? "password" : "text"}
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
                control={form.control}
                name="confirmPassword"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-xs">Confirm Password</FormLabel>
                    <FormControl>
                      <Input
                        type={showPassword ? "password" : "text"}
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
          className="text-sm font-semibold"
          disabled={isLoading}
          type="submit"
        >
          {isLoading && <Icons.spinner className="mr-2 size-4 animate-spin" />}
          Reset Password
        </Button>
      </form>
    </Form>
  )
}
