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
    <div className="border-b" {...props}>
      <div className="flex h-12 items-center px-4">
        <div className="flex items-center space-x-8">
          <Link href="/workflows">
            <Icons.logo className="ml-4 h-5 w-5" />
          </Link>
          <DynNav session={session} />
        </div>
        <div className="ml-auto flex items-center space-x-6">
          <UserNav />
        </div>
      </div>
    </div>
  )
}
