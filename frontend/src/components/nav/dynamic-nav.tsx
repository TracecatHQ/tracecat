"use client"

import React from "react"
import { type Params } from "next/dist/shared/lib/router/utils/route-matcher"
import Link from "next/link"
import { useParams, usePathname } from "next/navigation"
import { BlocksIcon, LibraryIcon, WorkflowIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import WorkflowNav from "@/components/nav/workflow-nav"

export default function DynamicNavbar() {
  const pathname = usePathname()
  const params = useParams()
  const DynNav = getNavBar(pathname, params)

  return <DynNav />
}

function getNavBar(pathname: string, params: Params) {
  if (pathname.startsWith("/workflows") && params.workflowId) {
    return WorkflowNav
  }
  return DashboardNav
}

function DashboardNav() {
  const pathname = usePathname()
  return (
    <nav className="flex space-x-4 lg:space-x-6">
      <Link
        href="/workflows"
        className={cn(
          "flex-cols flex items-center text-sm font-medium text-muted-foreground transition-colors hover:text-primary",
          pathname.startsWith("/workflows") && "text-primary"
        )}
      >
        <WorkflowIcon className="mr-2 size-4" />
        <span>Workflows</span>
      </Link>
      <Link
        href="/playbooks"
        className={cn(
          "flex-cols flex items-center text-sm font-medium text-muted-foreground transition-colors hover:text-primary",
          pathname.startsWith("/playbooks") && "text-primary"
        )}
      >
        <LibraryIcon className="mr-2 size-4" />
        <span>Playbooks</span>
      </Link>
      <Link
        href="https://docs.tracecat.com/integrations/introduction"
        target="_blank"
        className={cn(
          "flex-cols flex items-center text-sm font-medium text-muted-foreground transition-colors hover:text-primary",
          pathname.startsWith("/integrations") && "text-primary"
        )}
      >
        <BlocksIcon className="mr-2 size-4" />
        <span>Integrations</span>
      </Link>
    </nav>
  )
}
