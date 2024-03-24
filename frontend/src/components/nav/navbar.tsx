"use client"

import React from "react"
import Link from "next/link"
import { Session } from "@supabase/supabase-js"

import { Icons } from "@/components/icons"
import { CommunityNav } from "@/components/nav/community"
import DynamicNavbar from "@/components/nav/dyn-nav"
import UserNav from "@/components/nav/user-nav"

interface NavbarProps extends React.HTMLAttributes<HTMLDivElement> {
  session: Session | null
}
export default function Navbar({ session, ...props }: NavbarProps) {
  return (
    <div className="w-full space-x-8 border-b" {...props}>
      <div className="flex h-12 w-full items-center space-x-4 px-4">
        <Link href="/workflows">
          <Icons.logo className="h-5 w-5" />
        </Link>
        <DynamicNavbar session={session} />
        <div className="flex flex-1 items-center justify-end space-x-6">
          <CommunityNav />
          <UserNav />
        </div>
      </div>
    </div>
  )
}
