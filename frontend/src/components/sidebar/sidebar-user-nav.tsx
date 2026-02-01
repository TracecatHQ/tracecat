"use client"

import Cookies from "js-cookie"
import {
  BookText,
  ExternalLink,
  LogOut,
  LogOutIcon,
  ShieldCheckIcon,
  ShieldIcon,
  User,
} from "lucide-react"
import Link from "next/link"
import { useRouter } from "next/navigation"
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

const ORG_OVERRIDE_COOKIE = "tracecat-org-id"

export function SidebarUserNav() {
  const { user } = useAuth()
  const { logout } = useAuthActions()
  const router = useRouter()

  // Check if superuser is in org override mode
  const orgOverrideCookie = Cookies.get(ORG_OVERRIDE_COOKIE)
  const isInOrgOverrideMode = user?.isSuperuser && !!orgOverrideCookie

  const handleExitOrgContext = () => {
    Cookies.remove(ORG_OVERRIDE_COOKIE, { path: "/" })
    router.push("/admin/organizations")
  }

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
                email={user?.email ?? userDefaults.email}
                firstName={user?.firstName}
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
              <div className="flex flex-col space-y-1">
                <div className="flex items-center gap-2">
                  <p className="text-sm font-medium leading-none">
                    {displayName}
                  </p>
                  {user?.isSuperuser && (
                    <Badge
                      variant="outline"
                      className="h-4 px-1 text-[10px] font-normal"
                    >
                      Superuser
                    </Badge>
                  )}
                </div>
                <p className="text-xs leading-none text-muted-foreground">
                  {user?.email ?? userDefaults.email}
                </p>
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

            {user?.isSuperuser && (
              <>
                <DropdownMenuSeparator />
                <DropdownMenuGroup>
                  <Link href="/admin" className="w-full">
                    <DropdownMenuItem className="text-xs hover:cursor-pointer">
                      <ShieldCheckIcon className="mr-2 size-4" />
                      <span>Admin</span>
                    </DropdownMenuItem>
                  </Link>
                  {isInOrgOverrideMode && (
                    <DropdownMenuItem
                      className="text-xs hover:cursor-pointer"
                      onClick={handleExitOrgContext}
                    >
                      <LogOutIcon className="mr-2 size-4" />
                      <span>Exit org context</span>
                    </DropdownMenuItem>
                  )}
                </DropdownMenuGroup>
              </>
            )}

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
