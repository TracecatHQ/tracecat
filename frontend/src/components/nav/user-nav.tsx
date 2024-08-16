"use client"

import Link from "next/link"
import { useAuth } from "@/providers/auth"
import {
  BookText,
  ExternalLink,
  KeyRound,
  LogOut,
  Settings,
  UsersRound,
} from "lucide-react"

import { siteConfig } from "@/config/site"
import { userDefaults } from "@/config/user"
import { useWorkspace, useWorkspaceManager } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Icons } from "@/components/icons"
import UserAvatar from "@/components/user-avatar"

export default function UserNav() {
  const { user, logout } = useAuth()
  const { setLastWorkspaceId } = useWorkspaceManager()
  const { workspaceId } = useWorkspace()
  const workspaceUrl = `/workspaces/${workspaceId}`

  const handleLogout = async () => {
    setLastWorkspaceId()
    await logout()
  }
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="relative size-8 rounded-full">
          <UserAvatar alt={user?.first_name ?? undefined} />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-56 p-2" align="end" forceMount>
        <DropdownMenuLabel className="font-normal">
          <div className="flex flex-col space-y-1">
            <p className="text-sm font-medium leading-none">
              {user?.first_name ?? userDefaults.name}
            </p>
            <p className="text-xs leading-none text-muted-foreground">
              {user?.email.toString() ?? userDefaults.email}
            </p>
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <Link
            href={siteConfig.links.docs}
            target="_blank"
            className="my-2 w-full"
          >
            <DropdownMenuItem className="text-xs hover:cursor-pointer">
              <BookText className="mr-2 size-4" />
              Read the Docs
              <ExternalLink className="ml-auto size-3 text-muted-foreground" />
            </DropdownMenuItem>
          </Link>
          <Link
            href={siteConfig.links.docs}
            target="_blank"
            className="my-2 w-full"
          >
            <DropdownMenuItem className="text-xs hover:cursor-pointer">
              <Icons.gitHub className="mr-2 size-4" />
              Github Repository
              <ExternalLink className="ml-auto size-3 text-muted-foreground" />
            </DropdownMenuItem>
          </Link>
          <Link
            href={`${workspaceUrl}/settings/general`}
            className="my-2 w-full"
          >
            <DropdownMenuItem className="text-xs hover:cursor-pointer">
              <Settings className="mr-2 size-4" />
              Settings
            </DropdownMenuItem>
          </Link>
          <Link
            href={`${workspaceUrl}/settings/credentials`}
            className="my-2 w-full"
          >
            <DropdownMenuItem className="text-xs hover:cursor-pointer">
              <KeyRound className="mr-2 size-4" />
              <span>Credentials</span>
            </DropdownMenuItem>
          </Link>
          <DropdownMenuItem className="text-xs" disabled>
            <UsersRound className="mr-2 size-4" />
            <span>Manage users</span>
          </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="text-xs hover:cursor-pointer"
          onClick={handleLogout}
        >
          <LogOut className="mr-2 size-4" />
          <span>Logout</span>
        </DropdownMenuItem>
        <DropdownMenuSeparator />

        <DropdownMenuGroup>
          <Link
            href={siteConfig.links.discord}
            className="mt-1 w-full"
            target="_blank"
          >
            <Button className="w-full items-center text-xs">
              <Icons.discord className="mr-2 size-4 fill-white" />
              Join our Discord
            </Button>
          </Link>
        </DropdownMenuGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
