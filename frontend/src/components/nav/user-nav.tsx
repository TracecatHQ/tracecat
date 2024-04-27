"use client"

import Link from "next/link"
import { useRouter } from "next/navigation"
import { useClerk, useUser } from "@clerk/nextjs"
import { BookText, KeyRound, LogOut, Settings, UsersRound } from "lucide-react"

import { siteConfig } from "@/config/site"
import { userDefaults } from "@/config/user"
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
  const { user } = useUser()
  const { signOut } = useClerk()
  const router = useRouter()
  const handleSignOut = () => signOut(() => router.push("/"))
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="relative h-8 w-8 rounded-full">
          <UserAvatar
            src={user?.imageUrl}
            alt={user?.fullName || userDefaults.alt}
          />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-56 p-2" align="end" forceMount>
        <DropdownMenuLabel className="font-normal">
          <div className="flex flex-col space-y-1">
            <p className="text-sm font-medium leading-none">
              {user?.firstName ?? userDefaults.name}
            </p>
            <p className="text-xs leading-none text-muted-foreground">
              {user?.primaryEmailAddress?.toString() ?? userDefaults.email}
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
              <BookText className="mr-2 h-4 w-4" />
              Read the Docs
            </DropdownMenuItem>
          </Link>
          <Link href="/settings" className="my-2 w-full">
            <DropdownMenuItem className="text-xs hover:cursor-pointer">
              <Settings className="mr-2 h-4 w-4" />
              Settings
            </DropdownMenuItem>
          </Link>
          <Link href="/settings/credentials" className="my-2 w-full">
            <DropdownMenuItem className="text-xs hover:cursor-pointer">
              <KeyRound className="mr-2 h-4 w-4" />
              <span>Credentials</span>
            </DropdownMenuItem>
          </Link>
          <DropdownMenuItem className="text-xs" disabled>
            <UsersRound className="mr-2 h-4 w-4" />
            <span>Manage users</span>
          </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="text-xs hover:cursor-pointer"
          onClick={handleSignOut}
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
