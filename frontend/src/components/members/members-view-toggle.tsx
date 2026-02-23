"use client"

import { Layers, Shield, Users } from "lucide-react"
import Link from "next/link"
import { ScopeGuard } from "@/components/auth/scope-guard"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { cn } from "@/lib/utils"

export enum MembersViewMode {
  Members = "members",
  Roles = "roles",
  Groups = "groups",
}

interface MembersViewToggleProps {
  view: MembersViewMode
  className?: string
  membersHref: string
  rolesHref: string
  groupsHref: string
  /** The scope required to view roles and groups (e.g., "workspace:rbac:read" or "org:rbac:read") */
  rbacScope: string
}

export function MembersViewToggle({
  view,
  className,
  membersHref,
  rolesHref,
  groupsHref,
  rbacScope,
}: MembersViewToggleProps) {
  const toggleItems = [
    {
      mode: MembersViewMode.Members,
      icon: Users,
      tooltip: "Members",
      href: membersHref,
      ariaLabel: "Members view",
    },
    {
      mode: MembersViewMode.Roles,
      icon: Shield,
      tooltip: "Roles",
      href: rolesHref,
      ariaLabel: "Roles view",
    },
    {
      mode: MembersViewMode.Groups,
      icon: Layers,
      tooltip: "Groups",
      href: groupsHref,
      ariaLabel: "Groups view",
    },
  ] as const

  return (
    <ScopeGuard scope={rbacScope} fallback={null} loading={null}>
      <div
        className={cn(
          "inline-flex items-center rounded-md border bg-transparent",
          className
        )}
      >
        <TooltipProvider>
          {toggleItems.map((item, index) => {
            const Icon = item.icon
            const isActive = view === item.mode
            const isFirst = index === 0
            const isLast = index === toggleItems.length - 1
            const roundedClass = cn({
              "rounded-l-sm": isFirst,
              "rounded-none": !isFirst && !isLast,
              "rounded-r-sm": isLast,
            })
            const baseClasses = cn(
              "flex size-7 items-center justify-center transition-colors",
              roundedClass,
              isActive
                ? "bg-background text-accent-foreground"
                : "bg-accent text-muted-foreground hover:bg-muted/50"
            )

            return (
              <Tooltip key={item.mode}>
                <TooltipTrigger asChild>
                  <Link
                    href={item.href}
                    className={baseClasses}
                    aria-current={isActive ? "page" : undefined}
                    aria-label={item.ariaLabel}
                  >
                    <Icon className="size-3.5" />
                  </Link>
                </TooltipTrigger>
                <TooltipContent>
                  <p>{item.tooltip}</p>
                </TooltipContent>
              </Tooltip>
            )
          })}
        </TooltipProvider>
      </div>
    </ScopeGuard>
  )
}
