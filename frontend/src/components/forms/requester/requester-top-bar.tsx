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
import { useReadableWorkspaces } from "@/hooks/use-readable-workspaces"

/**
 * "Open workspace" items of the account menu, one per workspace where the
 * user holds `workspace:read`.
 *
 * A user who is intake-only in one workspace and a builder in another lands
 * here from the intake redirect; these items are their way back. Mounted
 * only while the menu is open, so a requester page asks nothing about
 * workspaces until then.
 */
function RequesterWorkspaceItems() {
  const workspaces = useReadableWorkspaces()
  if (workspaces.length === 0) {
    return null
  }
  return (
    <>
      <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
        Open workspace
      </DropdownMenuLabel>
      {workspaces.map((workspace) => (
        <DropdownMenuItem key={workspace.id} className="text-sm" asChild>
          <Link href={`/workspaces/${workspace.id}`}>
            <span className="truncate">{workspace.name}</span>
          </Link>
        </DropdownMenuItem>
      ))}
      <DropdownMenuSeparator />
    </>
  )
}

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
            <RequesterWorkspaceItems />
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
