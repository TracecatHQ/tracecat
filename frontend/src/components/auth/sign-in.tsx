"use client"

import React, { useState } from "react"
import Image from "next/image"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { useAuth } from "@/providers/auth"
import { zodResolver } from "@hookform/resolvers/zod"
import TracecatIcon from "public/icon.png"
import { useForm } from "react-hook-form"
import { z } from "zod"

import { authConfig } from "@/config/auth"
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
import { toast } from "@/components/ui/use-toast"
import { GoogleOAuthButton } from "@/components/auth/oauth-buttons"
import { SamlSSOButton } from "@/components/auth/saml"
import { Icons } from "@/components/icons"

export function SignIn({ className }: React.HTMLProps<HTMLDivElement>) {
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
          <CardTitle className="text-2xl">Sign into your account</CardTitle>
          <CardDescription>
            Select one of the authentication methods to proceed
          </CardDescription>
        </CardHeader>
        <CardContent className="flex-col space-y-2">
          {authConfig.authTypes.includes("basic") && <BasicLoginForm />}
          {authConfig.authTypes.length > 1 && (
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
          )}
          {authConfig.authTypes.includes("google_oauth") && (
            <GoogleOAuthButton className="w-full" />
          )}
          {authConfig.authTypes.includes("saml") && (
            <SamlSSOButton className="w-full" />
          )}
          {/* <GithubOAuthButton disabled className="hover:cur" /> */}
        </CardContent>
        {authConfig.authTypes.includes("basic") && (
          <CardFooter className="flex items-center justify-center text-sm text-muted-foreground">
            <div className="mt-4 text-center">
              Don&apos;t have an account?{" "}
              <Link href="/sign-up" className="underline">
                Sign up
              </Link>
            </div>
          </CardFooter>
        )}
      </div>
    </div>
  )
}

const basicLoginSchema = z.object({
  email: z.string().email().min(3, { message: "Required" }),
  password: z
    .string()
    .min(8, "Password needs to be at least 8 characters long"),
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
    try {
      setIsLoading(true)
      await login({
        formData: {
          username: values.email,
          password: values.password,
        },
      })
    } catch (error) {
      console.log("Error signing in", error)
      toast({
        title: "Error signing in",
        description: "Please check your email and password and try again",
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
            {isLoading && (
              <Icons.spinner className="mr-2 size-4 animate-spin" />
            )}
            Sign In
          </Button>
        </div>
      </form>
    </Form>
  )
}
