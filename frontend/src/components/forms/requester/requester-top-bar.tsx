"use client"

import { LogOutIcon } from "lucide-react"
import Link from "next/link"
import { requesterStyles } from "@/components/forms/form-styles"
import { Icons } from "@/components/icons"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import UserAvatar from "@/components/user-avatar"
import { useAuth, useAuthActions } from "@/hooks/use-auth"

/**
 * Top bar of the requester pages: the logo, a title that links back to the
 * catalog, and the account menu.
 */
export function RequesterTopBar({ title }: { title: string }) {
  const { user } = useAuth()
  const { logout } = useAuthActions()

  return (
    <header className={requesterStyles.topBar}>
      <Link
        href="/forms"
        aria-label={`${title}, all forms`}
        className="flex min-w-0 items-center gap-2.5"
      >
        <Icons.logo className="size-6 shrink-0" />
        <span className={requesterStyles.topBarTitle}>{title}</span>
      </Link>
      <span className="flex-1" />
      {user && (
        <DropdownMenu>
          <DropdownMenuTrigger
            aria-label={`Account menu for ${user.getDisplayName()}`}
            className="rounded-full focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
          >
            <UserAvatar
              email={user.email}
              firstName={user.firstName}
              className="size-7"
              fallbackClassName="text-xs"
            />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="flex flex-col gap-0.5 font-normal">
              <span className="truncate text-sm font-medium">
                {user.getDisplayName()}
              </span>
              <span className="truncate text-xs text-muted-foreground">
                {user.email}
              </span>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            <DropdownMenuItem className="text-sm" onSelect={() => logout()}>
              <LogOutIcon className="mr-2 size-4" />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </header>
  )
}
