"use client"

import {
  BookText,
  BracesIcon,
  ExternalLink,
  KeyRound,
  LogOut,
  UsersRound,
} from "lucide-react"
import Link from "next/link"
import { useParams } from "next/navigation"
import { Icons } from "@/components/icons"
import { Badge } from "@/components/ui/badge"
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
import UserAvatar from "@/components/user-avatar"
import { siteConfig } from "@/config/site"
import { userDefaults } from "@/config/user"
import { useAuth, useAuthActions } from "@/hooks/use-auth"

export default function UserNav() {
  const { user } = useAuth()
  const { logout } = useAuthActions()
  const params = useParams<{ workspaceId?: string }>()
  const workspaceId = params?.workspaceId
  const workspaceUrl = workspaceId ? `/workspaces/${workspaceId}` : null

  const handleLogout = async () => {
    await logout()
  }
  const displayName = user ? user.getDisplayName() : userDefaults.name
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="relative size-8 rounded-full">
          <UserAvatar alt={displayName} user={user} />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-56 p-2" align="end" forceMount>
        <DropdownMenuLabel className="font-normal">
          <div className="flex items-center justify-between">
            <div className="flex flex-col space-y-1">
              <p className="text-sm font-medium leading-none">{displayName}</p>
              <p className="text-xs leading-none text-muted-foreground">
                {user?.email.toString() ?? userDefaults.email}
              </p>
            </div>
            {user?.isPrivileged() && (
              <Badge
                variant="secondary"
                className="pointer-events-none font-medium capitalize"
              >
                {user?.role}
              </Badge>
            )}
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
              Read the docs
              <ExternalLink className="ml-auto size-3 text-muted-foreground" />
            </DropdownMenuItem>
          </Link>
          <Link
            href={siteConfig.links.github}
            target="_blank"
            className="my-2 w-full"
          >
            <DropdownMenuItem className="text-xs hover:cursor-pointer">
              <Icons.gitHub className="mr-2 size-4" />
              GitHub repository
              <ExternalLink className="ml-auto size-3 text-muted-foreground" />
            </DropdownMenuItem>
          </Link>
        </DropdownMenuGroup>
        {workspaceUrl && (
          <DropdownMenuGroup>
            <DropdownMenuSeparator />
            <DropdownMenuLabel className="text-xs text-muted-foreground">
              Workspace
            </DropdownMenuLabel>
            <Link href={`${workspaceUrl}/credentials`} className="my-2 w-full">
              <DropdownMenuItem className="text-xs hover:cursor-pointer">
                <KeyRound className="mr-2 size-4" />
                <span>Credentials</span>
              </DropdownMenuItem>
            </Link>
            <Link href={`${workspaceUrl}/members`} className="my-2 w-full">
              <DropdownMenuItem className="text-xs hover:cursor-pointer">
                <UsersRound className="mr-2 size-4" />
                <span>Manage members</span>
              </DropdownMenuItem>
            </Link>
            <Link
              href={`${workspaceUrl}/cases/custom-fields`}
              className="my-2 w-full"
            >
              <DropdownMenuItem className="text-xs hover:cursor-pointer">
                <BracesIcon className="mr-2 size-4" />
                <span>Custom fields</span>
              </DropdownMenuItem>
            </Link>
          </DropdownMenuGroup>
        )}
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
