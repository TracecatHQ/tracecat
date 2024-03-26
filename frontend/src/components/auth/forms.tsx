"use client"

import { useState } from "react"
import { Label } from "@radix-ui/react-label"

import { signInFlow, signInWithEmailMagicLink } from "@/lib/auth"
import { CardContent, CardFooter } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Icons } from "@/components/icons"

import { AlertLevel, AlertNotification } from "../notifications"
import { SubmitButton } from "./submit-button"

export function SignInForm({
  searchParams,
}: {
  searchParams: { level?: AlertLevel; message?: string }
}) {
  const [isLoading, setIsLoading] = useState<boolean>(false)
  async function onSubmit(event: React.SyntheticEvent<HTMLFormElement>) {
    event.preventDefault()
    setIsLoading(true)
    await signInFlow(new FormData(event.currentTarget))
    setIsLoading(false)
  }
  return (
    <form className="w-full" onSubmit={onSubmit}>
      <CardContent className="grid gap-4">
        <div className="grid gap-2">
          <Label className="text-sm" htmlFor="email">
            Email
          </Label>
          <Input
            id="email"
            className="rounded-md border bg-inherit px-4 py-2"
            name="email"
            placeholder="you@example.com"
            required
          />
        </div>

        <div className="grid gap-2">
          <Label className="text-sm" htmlFor="password">
            Password
          </Label>
          <Input
            id="password"
            className="rounded-md border bg-inherit px-4 py-2"
            type="password"
            name="password"
            placeholder="••••••••"
            required
          />
        </div>
      </CardContent>
      <CardFooter className="flex-col space-y-4">
        <SubmitButton
          className="w-full"
          pendingText="Signing In..."
          disabled={isLoading}
        >
          {isLoading && <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />}
          Sign In
        </SubmitButton>
        {searchParams?.message && (
          <AlertNotification
            level={searchParams?.level}
            message={searchParams.message}
          />
        )}
      </CardFooter>
    </form>
  )
}

export function PasswordlessSignInForm({
  searchParams,
}: {
  searchParams: { level?: AlertLevel; message?: string }
}) {
  const [isLoading, setIsLoading] = useState<boolean>(false)
  async function onSubmit(event: React.SyntheticEvent<HTMLFormElement>) {
    event.preventDefault()
    setIsLoading(true)
    await signInWithEmailMagicLink(new FormData(event.currentTarget))
    setIsLoading(false)
  }
  return (
    <form className="w-full" onSubmit={onSubmit}>
      <CardContent className="grid gap-4">
        <div className="grid gap-2">
          <Label className="text-sm" htmlFor="email">
            Email
          </Label>
          <Input
            id="email"
            className="rounded-md border bg-inherit px-4 py-2"
            name="email"
            placeholder="you@example.com"
            required
          />
        </div>
      </CardContent>
      <CardFooter className="flex-col space-y-4">
        <SubmitButton
          className="w-full"
          pendingText="Signing In..."
          disabled={isLoading}
        >
          {isLoading && <Icons.spinner className="mr-2 h-4 w-4 animate-spin" />}
          Sign In
        </SubmitButton>

        {searchParams?.message && (
          <AlertNotification
            level={searchParams?.level}
            message={searchParams.message}
          />
        )}
      </CardFooter>
    </form>
  )
}
