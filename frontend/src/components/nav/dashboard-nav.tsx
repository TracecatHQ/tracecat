"use client"

import React from "react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { BlocksIcon, LibraryIcon } from "lucide-react"

import { cn } from "@/lib/utils"

export function DashboardNav() {
  const pathname = usePathname()
  return (
    <nav className="flex space-x-4 lg:space-x-6">
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
