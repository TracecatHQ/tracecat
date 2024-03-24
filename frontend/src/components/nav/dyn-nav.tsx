"use client"

import React from "react"
import { type Params } from "next/dist/shared/lib/router/utils/route-matcher"
import Link from "next/link"
import { useParams, usePathname } from "next/navigation"
import { Session } from "@supabase/supabase-js"

import { cn } from "@/lib/utils"
import WorkflowsNav from "@/components/nav/workflows-nav"

interface NavbarProps extends React.HTMLAttributes<HTMLDivElement> {
  session: Session | null
}
export default function DynamicNavbar({ session, ...props }: NavbarProps) {
  const pathname = usePathname()
  const params = useParams()
  const DynNav = getNavBar(pathname, params)

  return <DynNav session={session} />
}

function getNavBar(pathname: string, params: Params) {
  if (pathname.startsWith("/workflows") && params.workflowId) {
    return WorkflowsNav
  }
  return DashboardNav
}

function DashboardNav() {
  const pathname = usePathname()
  return (
    <nav className="flex items-center space-x-4 lg:space-x-6">
      <Link
        href="/workflows"
        className={cn(
          "text-sm font-medium text-muted-foreground transition-colors hover:text-primary",
          pathname.startsWith("/workflows") && "text-primary"
        )}
      >
        Workflows
      </Link>
      <Link
        href="/library"
        className={cn(
          "text-sm font-medium text-muted-foreground transition-colors hover:text-primary",
          pathname.startsWith("/integrations") && "text-primary"
        )}
      >
        Library
      </Link>
    </nav>
  )
}
