"use client"

import Link from "next/link"
import { useParams, usePathname } from "next/navigation"
import { Icons } from "@/components/icons"
import { BuilderNav } from "@/components/nav/builder-nav"
import { TooltipProvider } from "@/components/ui/tooltip"

type DynamicNavbarParams = {
  workspaceId?: string
  workflowId?: string
}

export function DynamicNavbar() {
  const pathname = usePathname()
  const params = useParams<DynamicNavbarParams>()
  const workspaceId = params?.workspaceId
  const workflowId = params?.workflowId

  // Only show navbar for workflow builder
  if (
    !pathname ||
    !pathname.includes("/workflows") ||
    !workspaceId ||
    !workflowId
  ) {
    return null
  }

  return (
    <TooltipProvider delayDuration={300}>
      <div className="w-full space-x-8 border-b">
        <div className="flex h-10 w-full items-center space-x-5 px-5">
          <Link href="/workspaces">
            <Icons.logo className="size-5" />
          </Link>
          <BuilderNav />
        </div>
      </div>
    </TooltipProvider>
  )
}
