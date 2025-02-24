"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { useWorkspace } from "@/providers/workspace"
import { BuildingIcon, CircleUserRoundIcon, LucideIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import { buttonVariants } from "@/components/ui/button"
import { Label } from "@/components/ui/label"

const accountNavItems: NavItem[] = [
  {
    title: "Profile",
    href: "/settings/profile",
  },
  {
    title: "Security",
    href: "/settings/security",
  },
]
const workspaceNavItems: NavItem[] = [
  {
    title: "General",
    href: "/settings/general",
  },
  {
    title: "Credentials",
    href: "/settings/credentials",
  },
  {
    title: "Members",
    href: "/settings/members",
  },
]

type NavItem = {
  title: string
  href: string
}
interface SidebarNavProps extends React.HTMLAttributes<HTMLElement> {
  title: string
  icon: LucideIcon
  items: NavItem[]
}

export function SidebarNav() {
  return (
    <div className="h-full space-y-4 pr-4 pt-16">
      <div className="space-y-0.5">
        <h2 className="text-xl font-bold tracking-tight">Settings</h2>
      </div>
      <SidebarNavBlock
        title="Workspace"
        icon={BuildingIcon}
        items={workspaceNavItems}
      />
      <SidebarNavBlock
        title="Account"
        icon={CircleUserRoundIcon}
        items={accountNavItems}
      />
    </div>
  )
}
const defaultRoute = "general"

export function SidebarNavBlock({
  className,
  title,
  items,
  icon: Icon,
  ...props
}: SidebarNavProps) {
  const pathname = usePathname()
  const { workspaceId } = useWorkspace()
  const workspaceUrl = `/workspaces/${workspaceId}`
  const leafRoute = pathname.split("/").pop()

  return (
    <nav
      className={cn(
        "flex space-x-2 lg:flex-col lg:space-x-0 lg:space-y-1",
        className
      )}
      {...props}
    >
      <Label className="flex items-center py-2 text-muted-foreground">
        <Icon className="mr-2 size-4" />
        <span>{title}</span>
      </Label>
      {items.map((item) => (
        <Link
          key={item.href}
          href={`${workspaceUrl}/${item.href}`}
          className={cn(
            buttonVariants({ variant: "ghost" }),
            item.href.endsWith(leafRoute ?? defaultRoute) &&
              "bg-muted-foreground/10",
            "h-8 justify-start hover:cursor-default"
          )}
        >
          {item.title}
        </Link>
      ))}
    </nav>
  )
}
