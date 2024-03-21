"use client"

import Link from "next/link"
import { useSessionContext } from "@/providers/session"
import { User } from "@supabase/supabase-js"
import { KeyRound, LogOut, Settings, UsersRound } from "lucide-react"

import { siteConfig } from "@/config/site"
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
import {
  NewCredentialsDialog,
  NewCredentialsDialogTrigger,
} from "@/components/new-credential-dialog"
import UserAvatar from "@/components/user-avatar"

const userDefaults = {
  name: "Hello, friend!",
  email: "friend@example.com",
  avatarUrl:
    "https://gravatar.com/avatar/fb1a12daafe05ae4b59489de1ab63026?s=400&d=robohash&r=x",
  alt: "@friend",
}
export default function UserNav() {
  const { session, signOut } = useSessionContext()
  const user = session?.user as User | null
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="relative h-8 w-8 rounded-full">
          <UserAvatar
            src={user?.user_metadata?.avatar_url || userDefaults.avatarUrl}
            alt={user?.user_metadata?.alt || userDefaults.alt}
          />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-56 p-2" align="end" forceMount>
        <DropdownMenuLabel className="font-normal">
          <div className="flex flex-col space-y-1">
            <p className="text-sm font-medium leading-none">
              {user?.user_metadata.name ?? userDefaults.name}
            </p>
            <p className="text-xs leading-none text-muted-foreground">
              {user?.email ?? userDefaults.email}
            </p>
          </div>
        </DropdownMenuLabel>
        <DropdownMenuSeparator />
        <DropdownMenuGroup>
          <DropdownMenuItem className="text-xs opacity-50">
            <Settings className="mr-2 h-4 w-4" />
            Settings
          </DropdownMenuItem>
          <NewCredentialsDialog>
            <NewCredentialsDialogTrigger asChild>
              <Button
                className="h-8 w-full justify-start p-2 text-xs hover:cursor-pointer"
                variant="ghost"
              >
                <KeyRound className="mr-2 h-4 w-4" />
                <span>Credentials</span>
              </Button>
            </NewCredentialsDialogTrigger>
          </NewCredentialsDialog>
          <DropdownMenuItem className="text-xs opacity-50">
            <UsersRound className="mr-2 h-4 w-4" />
            <span>Manage users</span>
          </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="text-xs hover:cursor-pointer"
          onClick={signOut}
        >
          <LogOut className="mr-2 h-4 w-4" />
          <span>Logout</span>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <Link href={siteConfig.links.discord} className="mt-1 w-full">
          <Button className="w-full items-center text-xs">
            <Icons.discord className="mr-2 h-4 w-4 fill-white" />
            Join our Discord
          </Button>
        </Link>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
