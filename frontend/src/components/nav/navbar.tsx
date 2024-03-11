import React from "react"
import Link from "next/link"
import { Session } from "@supabase/supabase-js"

import { Icons } from "@/components/icons"
import UserNav from "@/components/nav/user-nav"

import WorkflowsNav from "./workflows-nav"

const DynamicNavBars = {
  workflows: WorkflowsNav,
}

interface NavbarProps extends React.HTMLAttributes<HTMLDivElement> {
  session: Session | null
}
export default async function Navbar({ session, ...props }: NavbarProps) {
  const DynNav = DynamicNavBars["workflows"]
  return (
    <div className="w-full space-x-4 border-b" {...props}>
      <div className="flex h-12 w-full items-center space-x-4 px-4">
        <Link href="/workflows">
          <Icons.logo className="ml-4 h-5 w-5" />
        </Link>
        <DynNav session={session} />
        <div className="flex flex-1 items-center justify-end space-x-6">
          <UserNav />
        </div>
      </div>
    </div>
  )
}
