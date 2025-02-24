"use client"

import Link from "next/link"
import { usePathname } from "next/navigation"
import { LibraryBigIcon, LucideIcon } from "lucide-react"

import { cn } from "@/lib/utils"
import { buttonVariants } from "@/components/ui/button"
import { Label } from "@/components/ui/label"

type NavItem = {
  title: string
  href: string
}

const navItems: NavItem[] = [
  {
    title: "Actions",
    href: "/registry/actions",
  },
  {
    title: "Repositories",
    href: "/registry/repositories",
  },
]

interface SidebarNavProps extends React.HTMLAttributes<HTMLElement> {
  title: string
  icon: LucideIcon
  items: NavItem[]
}

export function RegistrySidebarNav() {
  return (
    <div className="h-full space-y-4 pr-4 pt-16">
      <div className="space-y-0.5">
        <h2 className="text-xl font-bold tracking-tight">Registry</h2>
      </div>
      <SidebarNavBlock
        title="Registry"
        icon={LibraryBigIcon}
        items={navItems}
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
  const leafRoute = pathname?.split("/").pop()

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
          href={item.href}
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
