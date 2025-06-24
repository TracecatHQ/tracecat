"use client"

import { BuildingIcon, type LucideIcon } from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { buttonVariants } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"
import { useWorkspace } from "@/providers/workspace"

const navItems: NavItem[] = [
  {
    title: "Cases",
    href: "/cases",
  },
  {
    title: "Custom Fields",
    href: "/cases/fields",
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

export function CasesSidebar() {
  return (
    <div className="h-full space-y-4 pr-4 pt-16">
      <div className="space-y-0.5">
        <h2 className="text-xl font-bold tracking-tight">Cases</h2>
      </div>
      <SidebarNavBlock title="Cases" icon={BuildingIcon} items={navItems} />
    </div>
  )
}
const defaultRoute = "cases"

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
  const leafRoute = pathname?.split("/").pop()

  return (
    <nav
      className={cn("flex  flex-col space-x-0 space-y-1", className)}
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
