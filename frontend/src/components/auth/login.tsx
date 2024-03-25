import React from "react"
import Image from "next/image"
// import Link from "next/link"
import { redirect } from "next/navigation"
import { createClient } from "@/utils/supabase/server"
import TracecatIcon from "public/icon.png"

import {
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { PasswordlessSignInForm, SignInForm } from "@/components/auth/forms"
import {
  GithubOAuthButton,
  GoogleOAuthButton,
} from "@/components/auth/oauth-buttons"

export default async function Login({
  searchParams,
}: {
  searchParams: { message: string }
}) {
  const supabase = createClient()
  const {
    data: { session },
  } = await supabase.auth.getSession()

  if (session) {
    return redirect("/workflows")
  }

  return (
    <div className="container flex h-full w-full items-center justify-center">
      <div className="flex w-full flex-1 flex-col justify-center gap-2 px-8 sm:max-w-md">
        {/* <Link
          href="/"
          className="bg-btn-background hover:bg-btn-background-hover group absolute left-8 top-8 flex items-center rounded-md px-4 py-2 text-sm text-foreground no-underline"
        >
          <svg
            xmlns="http://www.w3.org/2000/svg"
            width="24"
            height="24"
            viewBox="0 0 24 24"
            fill="none"
            stroke="currentColor"
            strokeWidth="2"
            strokeLinecap="round"
            strokeLinejoin="round"
            className="mr-2 h-4 w-4 transition-transform group-hover:-translate-x-1"
          >
            <polyline points="15 18 9 12 15 6" />
          </svg>{" "}
          Back
        </Link> */}
        <CardHeader className="items-center space-y-2 text-center">
          <Image src={TracecatIcon} alt="Tracecat" className="mb-8 h-16 w-16" />
          <CardTitle className="text-2xl">Sign into your account</CardTitle>
          <CardDescription>
            Enter your email below to create your account or sign in
          </CardDescription>
        </CardHeader>
        {Boolean(process.env.NEXT_PUBLIC_SELF_HOSTED) ? (
          <SignInForm searchParams={searchParams} />
        ) : (
          <>
            <CardContent className="flex-col space-y-2">
              <div className="mb-8 grid grid-cols-2 gap-2">
                <GoogleOAuthButton />
                <GithubOAuthButton />
              </div>
              <div className="relative">
                <div className="absolute inset-0 flex items-center">
                  <span className="w-full border-t" />
                </div>
                <div className="relative flex justify-center text-xs uppercase">
                  <span className="bg-background px-2 text-muted-foreground">
                    Or continue with
                  </span>
                </div>
              </div>
            </CardContent>
            <PasswordlessSignInForm searchParams={searchParams} />
          </>
        )}
      </div>
    </div>
  )
}
