"use client"

import {
  KeyRoundIcon,
  type LucideIcon,
  SettingsIcon,
  UsersIcon,
} from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { buttonVariants } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import { cn } from "@/lib/utils"

type NavItem = {
  title: string
  href: string
}

const settingsNavItems: NavItem[] = [
  {
    title: "Git repository",
    href: "/organization/settings/git",
  },
  {
    title: "Single sign-on",
    href: "/organization/settings/sso",
  },
  {
    title: "OAuth",
    href: "/organization/settings/oauth",
  },
  {
    title: "Email authentication",
    href: "/organization/settings/auth",
  },
  {
    title: "Application",
    href: "/organization/settings/app",
  },
]

const secretNavItems: NavItem[] = [
  {
    title: "SSH keys",
    href: "/organization/ssh-keys",
  },
]

const userNavItems: NavItem[] = [
  {
    title: "Members",
    href: "/organization/members",
  },
  {
    title: "Sessions",
    href: "/organization/sessions",
  },
]

interface SidebarNavProps extends React.HTMLAttributes<HTMLElement> {
  title: string
  icon: LucideIcon
  items: NavItem[]
}

export function OrganizationSidebarNav() {
  return (
    <div className="h-full space-y-4 pr-4 pt-16">
      <div className="space-y-0.5">
        <h2 className="text-xl font-bold tracking-tight">Organization</h2>
      </div>
      <SidebarNavBlock
        title="Settings"
        icon={SettingsIcon}
        items={settingsNavItems}
      />
      <SidebarNavBlock
        title="Secrets"
        icon={KeyRoundIcon}
        items={secretNavItems}
      />
      <SidebarNavBlock title="Users" icon={UsersIcon} items={userNavItems} />
    </div>
  )
}

export function SidebarNavBlock({
  className,
  title,
  items,
  icon: Icon,
  ...props
}: SidebarNavProps) {
  const pathname = usePathname()

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
            pathname === item.href && "bg-muted-foreground/10",
            "h-8 justify-start hover:cursor-default"
          )}
        >
          {item.title}
        </Link>
      ))}
    </nav>
  )
}
