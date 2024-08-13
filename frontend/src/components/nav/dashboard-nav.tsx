"use client"

import React from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { BlocksIcon, LibraryIcon, WorkflowIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import { WorkspaceSelector } from "@/components/workspaces/workspace-selector"

export function DashboardNav() {
  const pathname = usePathname()
  return (
    <nav className="flex space-x-4 lg:space-x-6">
      <WorkspaceSelector />
      <Link
        href="/"
        className={cn(
          "flex-cols flex items-center text-sm font-medium text-muted-foreground transition-colors hover:text-primary",
          pathname.endsWith("/workflows") && "text-primary"
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
