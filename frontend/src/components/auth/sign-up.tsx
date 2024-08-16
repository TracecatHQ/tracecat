"use client"

import React, { useState } from "react"
import Image from "next/image"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { ApiError } from "@/client"
import { useAuth } from "@/providers/auth"
import { zodResolver } from "@hookform/resolvers/zod"
import TracecatIcon from "public/icon.png"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from "@/components/ui/form"
import { Input } from "@/components/ui/input"
import { Icons } from "@/components/icons"

export function SignUp({ className }: React.HTMLProps<HTMLDivElement>) {
  const { user } = useAuth()
  const router = useRouter()
  if (user) {
    router.push("/workspaces")
  }

  return (
    <div
      className={cn(
        "container flex size-full items-center justify-center",
        className
      )}
    >
      <div className="flex w-full flex-1 flex-col justify-center gap-2 px-8 sm:max-w-md">
        <CardHeader className="items-center space-y-2 text-center">
          <Image src={TracecatIcon} alt="Tracecat" className="mb-8 size-16" />
          <CardTitle className="text-2xl">Create an account</CardTitle>
          <CardDescription>
            Enter your email below to create an account
          </CardDescription>
        </CardHeader>
        <CardContent>
          <div className="grid gap-4">
            <BasicRegistrationForm />
          </div>
          <div className="mt-4 text-center text-sm text-muted-foreground">
            Already have an account?{" "}
            <Link href="/sign-in" className="underline">
              Sign in
            </Link>
          </div>
        </CardContent>
      </div>
    </div>
  )
}

const basicRegistrationSchema = z.object({
  email: z.string().email().min(3, { message: "Required" }),
  password: z.string().min(8, "Password needs to be atleast 8 charakters long"),
})
type BasicLoginForm = z.infer<typeof basicRegistrationSchema>

export function BasicRegistrationForm() {
  const [isLoading, setIsLoading] = useState(false)
  const { register, login } = useAuth()
  const form = useForm<BasicLoginForm>({
    resolver: zodResolver(basicRegistrationSchema),
    defaultValues: {
      email: "",
      password: "",
    },
  })

  const onSubmit = async (values: BasicLoginForm) => {
    console.log(values)
    try {
      setIsLoading(true)
      /**
       * XXX(auth): The user cannot set is_active or is_superuser itself at registration.
       * Only a superuser can do it by PATCHing the user.
       */
      await register({
        requestBody: {
          email: values.email,
          password: values.password,
        },
      })
      // On successful registration, log the user in
      await login({
        formData: {
          username: values.email,
          password: values.password,
        },
      })
    } catch (error) {
      if (error instanceof ApiError) {
        console.error("ApiError registering user", error)
      } else {
        console.error("Error registering user", error)
        throw error
      }
    } finally {
      setIsLoading(false)
    }
  }
  return (
    <Form {...form}>
      <form onSubmit={form.handleSubmit(onSubmit)} className="space-y-8">
        <div className="flex w-full flex-col space-y-4">
          <div className="grid gap-4">
            <div className="grid gap-2">
              <FormField
                control={form.control}
                name="email"
                render={({ field }) => (
                  <FormItem>
                    <FormLabel className="text-xs">Email</FormLabel>
                    <FormControl>
                      <Input placeholder="user@company.com" {...field} />
                    </FormControl>
                    <FormMessage />
                  </FormItem>
                )}
              />
            </div>
            <div className="grid gap-2">
              <div className="grid gap-2">
                <FormField
                  control={form.control}
                  name="password"
                  render={({ field }) => (
                    <FormItem>
                      <FormLabel className="text-xs">Password</FormLabel>
                      <FormControl>
                        <Input
                          type="password"
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
          <Button className="w-full text-sm" disabled={isLoading} type="submit">
            {isLoading && (
              <Icons.spinner className="mr-2 size-4 animate-spin" />
            )}
            Create Account
          </Button>
        </div>
      </form>
    </Form>
  )
}
