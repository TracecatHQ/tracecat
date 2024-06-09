// UNPROTECTED ROUTE
import React from "react"
import { type Metadata } from "next"
import Image from "next/image"
import Link from "next/link"
import { redirect } from "next/navigation"
import TracecatIcon from "public/icon.png"

import { auth } from "@/lib/auth"
import { cn } from "@/lib/utils"
import { LogInIcon } from "lucide-react"
import { buttonVariants } from "@/components/ui/button"
import { Icons } from "@/components/icons"
import PrivacyPolicy from "@/components/privacy-policy"

export const metadata: Metadata = {
  title: "Tracecat",
  description: "Open Source automation platform for security alerts",
}
export default async function HomePage() {
  const { userId } = auth()
  if (userId) {
    return redirect("/workflows")
  }
  return (
    <>
      <div className="container relative hidden h-full select-none flex-col items-center justify-center md:grid lg:max-w-none lg:grid-cols-2 lg:px-0">
        <div
          className={cn(
            buttonVariants({ variant: "ghost" }),
            "absolute right-4 top-4 md:right-8 md:top-8"
          )}
        >
          <Link href="/sign-in" className="flex-column flex">
            <LogInIcon className="mr-3 size-5" />
            <span>Sign In</span>
          </Link>
        </div>
        <div className="relative hidden h-full flex-col bg-muted p-10 text-white dark:border-r lg:flex">
          <div className="absolute inset-0 bg-zinc-900" />
          <div className="relative z-20 flex items-center text-xl font-semibold tracking-wider">
            <Icons.logo className="mr-4 size-6" />
            <h1>Tracecat</h1>
          </div>
        </div>
        <div className="lg:p-8">
          <div className="mx-auto flex w-full flex-col justify-center space-y-8 text-center sm:w-[350px]">
            <Image
              src={TracecatIcon}
              alt="Tracecat"
              className="mx-auto size-16"
            />
            <h2 className="text-md text-muted-foreground">
              The modern security automation platform
              designed to reduce noise.
            </h2>
            <PrivacyPolicy />
          </div>
        </div>
      </div>
    </>
  )
}
