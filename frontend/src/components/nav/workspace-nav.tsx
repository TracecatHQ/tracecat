"use client"

import React from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { useWorkspace } from "@/providers/workspace"
import { WorkflowIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import {
  OrganizationNavButton,
  RegistryNavButton,
} from "@/components/nav/nav-buttons"
import { WorkspaceSelector } from "@/components/workspaces/workspace-selector"

export function WorkspaceNav() {
  const { workspaceId } = useWorkspace()
  const pathname = usePathname()
  return (
    <nav className="flex space-x-4 lg:space-x-6">
      <div className="md:min-w-[150px] md:max-w-[200px] lg:min-w-[250px] lg:max-w-[300px]">
        <WorkspaceSelector />
      </div>
      <Link
        href={`/workspaces/${workspaceId}/workflows`}
        className={cn(
          "flex-cols flex items-center text-sm font-medium text-muted-foreground transition-colors hover:text-primary",
          pathname.endsWith("/workflows") && "text-primary"
        )}
      >
        <WorkflowIcon className="mr-2 size-4" />
        <span>Workflows</span>
      </Link>
      <RegistryNavButton />
      <OrganizationNavButton />
    </nav>
  )
}
