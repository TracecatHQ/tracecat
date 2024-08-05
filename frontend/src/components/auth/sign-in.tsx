"use client"

import React, { useState } from "react"
import Image from "next/image"
import Link from "next/link"
import { redirect } from "next/navigation"
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
  CardFooter,
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
import { GoogleOAuthButton } from "@/components/auth/oauth-buttons"
import { Icons } from "@/components/icons"

export function SignIn({ className }: React.HTMLProps<HTMLDivElement>) {
  const { user } = useAuth()
  if (user) {
    return redirect("/workflows")
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
          <CardTitle className="text-2xl">Sign into your account</CardTitle>
          <CardDescription>
            Enter your email below to sign in to your account
          </CardDescription>
        </CardHeader>
        <CardContent className="flex-col space-y-2">
          <BasicLoginForm />
          <div className="relative">
            <div className="absolute inset-0 flex items-center">
              <span className="w-full border-t" />
            </div>
            <div className="relative my-6 flex justify-center text-xs uppercase">
              <span className="bg-background px-2 text-muted-foreground">
                Or continue with
              </span>
            </div>
          </div>
          <GoogleOAuthButton className="w-full" />
          {/* <GithubOAuthButton disabled className="hover:cur" /> */}
        </CardContent>
        <CardFooter className="flex items-center justify-center text-sm text-muted-foreground">
          <div className="mt-4 text-center">
            Don&apos;t have an account?{" "}
            <Link href="/sign-up" className="underline">
              Sign up
            </Link>
          </div>
        </CardFooter>
      </div>
    </div>
  )
}

const basicLoginSchema = z.object({
  email: z.string().email().min(3, { message: "Required" }),
  password: z.string().min(8, "Password needs to be atleast 8 charakters long"),
})
type BasicLoginForm = z.infer<typeof basicLoginSchema>

export function BasicLoginForm() {
  const [isLoading, setIsLoading] = useState(false)
  const { login } = useAuth()
  const form = useForm<BasicLoginForm>({
    resolver: zodResolver(basicLoginSchema),
    defaultValues: {
      email: "",
      password: "",
    },
  })

  const onSubmit = async (values: BasicLoginForm) => {
    console.log(values)
    try {
      setIsLoading(true)
      await login({
        formData: {
          username: values.email,
          password: values.password,
        },
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
            {isLoading ? (
              <span>
                <Icons.spinner className="mr-2 size-4 animate-spin" />
                Signing In...
              </span>
            ) : (
              <span>Sign In</span>
            )}
          </Button>
          {/* <Button type="submit" className="w-full">
            Sign In
          </Button> */}
        </div>
      </form>
    </Form>
  )
}
