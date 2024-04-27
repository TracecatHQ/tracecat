// UNPROTECTED ROUTE
import React from "react"
import { type Metadata } from "next"
import Image from "next/image"
import Link from "next/link"
import { redirect } from "next/navigation"
import { auth } from "@clerk/nextjs/server"
import TracecatIcon from "public/icon.png"

import { cn } from "@/lib/utils"
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
          <Link href="/sign-in">Sign In</Link>
        </div>
        <div className="relative hidden h-full flex-col bg-muted p-10 text-white dark:border-r lg:flex">
          <div className="absolute inset-0 bg-zinc-900" />
          <div className="relative z-20 flex items-center text-lg font-semibold tracking-wider">
            <Icons.logo className="mr-1 size-5" />
            Tracecat
          </div>
          <div className="relative z-20 mt-auto">
            <blockquote className="space-y-2">
              <p className="text-md italic">
                &ldquo;Make something people want.&rdquo;
              </p>
              {/* <footer className="text-sm">Paul Graham</footer> */}
            </blockquote>
          </div>
        </div>
        <div className="lg:p-8">
          <div className="mx-auto flex w-full flex-col justify-center space-y-6 sm:w-[350px]">
            <div className="flex flex-col space-y-4 text-center">
              <Image
                src={TracecatIcon}
                alt="Tracecat"
                className="mx-auto h-16 w-16"
              />
              <h1 className="text-2xl font-semibold">Tracecat Cloud</h1>
              <p className="text-md text-muted-foreground">Public Alpha v2.0</p>
            </div>

            <PrivacyPolicy />
          </div>
        </div>
      </div>
    </>
  )
}
