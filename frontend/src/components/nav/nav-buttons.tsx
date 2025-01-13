"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { BuildingIcon, ChevronLeftIcon, LibraryBigIcon } from "lucide-react"

import { cn } from "@/lib/utils"

export function RegistryNavButton() {
  const pathname = usePathname()
  return (
    <Link
      href={"/registry"}
      className={cn(
        "flex-cols flex items-center text-sm font-medium text-muted-foreground transition-colors hover:text-primary",
        pathname.startsWith("/registry") && "text-primary"
      )}
    >
      <LibraryBigIcon className="mr-2 size-4" />
      <span>Registry</span>
    </Link>
  )
}

export function OrganizationNavButton() {
  const pathname = usePathname()
  return (
    <Link
      href={"/organization"}
      className={cn(
        "flex-cols flex items-center text-sm font-medium text-muted-foreground transition-colors hover:text-primary",
        pathname.startsWith("/organization") && "text-primary"
      )}
    >
      <BuildingIcon className="mr-2 size-4" />
      <span>Organization</span>
    </Link>
  )
}

export function BackToWorkspaceNavButton() {
  return (
    <Link href="/workspaces" className="flex items-center space-x-1">
      <ChevronLeftIcon className="size-4 text-foreground/80" />
      <span className="text-sm text-muted-foreground">Workspaces</span>
    </Link>
  )
}
