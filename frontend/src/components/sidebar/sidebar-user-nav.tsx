"use client"

import { BookText, ExternalLink, LogOut, ShieldIcon, User } from "lucide-react"
import Link from "next/link"
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
import { SidebarMenu, SidebarMenuItem } from "@/components/ui/sidebar"
import UserAvatar from "@/components/user-avatar"
import { siteConfig } from "@/config/site"
import { userDefaults } from "@/config/user"
import { useAuth, useAuthActions } from "@/hooks/use-auth"

export function SidebarUserNav() {
  const { user } = useAuth()
  const { logout } = useAuthActions()

  const handleLogout = async () => {
    await logout()
  }
  const displayName = user ? user.getDisplayName() : userDefaults.name

  return (
    <SidebarMenu>
      <SidebarMenuItem>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="outline" size="icon" className="h-9 w-9">
              <UserAvatar
                alt={displayName}
                user={user}
                className="h-6 w-6 rounded-full"
              />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent
            className="w-[--radix-dropdown-menu-trigger-width] min-w-[220px] rounded-lg"
            side="right"
            align="end"
            sideOffset={4}
          >
            <DropdownMenuLabel className="font-normal">
              <div className="flex items-center justify-between">
                <div className="flex flex-col space-y-1">
                  <p className="text-sm font-medium leading-none">
                    {displayName}
                  </p>
                  <p className="text-xs leading-none text-muted-foreground">
                    {user?.email?.toString() ?? userDefaults.email}
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
              <Link href="/profile/settings" className="w-full">
                <DropdownMenuItem className="text-xs hover:cursor-pointer">
                  <User className="mr-2 size-4" />
                  <span>Account</span>
                </DropdownMenuItem>
              </Link>
              <Link href="/profile/security" className="w-full">
                <DropdownMenuItem className="text-xs hover:cursor-pointer">
                  <ShieldIcon className="mr-2 size-4" />
                  <span>Security</span>
                </DropdownMenuItem>
              </Link>
            </DropdownMenuGroup>

            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <Link
                href={siteConfig.links.docs}
                target="_blank"
                rel="noopener noreferrer"
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
                rel="noopener noreferrer"
                className="my-2 w-full"
              >
                <DropdownMenuItem className="text-xs hover:cursor-pointer">
                  <Icons.gitHub className="mr-2 size-4" />
                  GitHub repository
                  <ExternalLink className="ml-auto size-3 text-muted-foreground" />
                </DropdownMenuItem>
              </Link>
            </DropdownMenuGroup>

            <DropdownMenuSeparator />
            <DropdownMenuItem
              className="text-xs hover:cursor-pointer"
              onClick={handleLogout}
            >
              <LogOut className="mr-2 size-4" />
              <span>Logout</span>
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </SidebarMenuItem>
    </SidebarMenu>
  )
}
