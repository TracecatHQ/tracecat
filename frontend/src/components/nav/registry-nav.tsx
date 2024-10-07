"use client"

import Link from "next/link"
import { ChevronLeftIcon } from "lucide-react"

export function RegistryNav() {
  return (
    <nav className="flex space-x-4 lg:space-x-6">
      <Link href="/workspaces" className="flex items-center space-x-1">
        <ChevronLeftIcon className="size-4 text-foreground/80" />
        <span className="text-sm text-muted-foreground">Workspaces</span>
      </Link>
    </nav>
  )
}
