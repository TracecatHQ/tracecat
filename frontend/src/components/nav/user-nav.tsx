"use client"

import { useSessionContext } from "@/providers/session"
import { User } from "@supabase/supabase-js"
import { KeyRound, LogOut, Settings, UsersRound } from "lucide-react"

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

const userDefaults = {
  name: "Test User",
  email: "name@example.com",
  avatarUrl:
    "https://media.licdn.com/dms/image/C5103AQEXlYZeTKuwyQ/profile-displayphoto-shrink_200_200/0/1582770649112?e=1715212800&v=beta&t=wqVZfVV4YwedybQFzKazeWmlQslMQ11t_NGMCqwpN-k",
  alt: "@daryllimyt",
}
export default function UserNav() {
  const { session, signOut } = useSessionContext()
  const user = session?.user as User | null
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="relative h-8 w-8 rounded-full">
          <UserAvatar src={userDefaults.avatarUrl} alt={userDefaults.alt} />
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
          <DropdownMenuItem className="text-xs hover:cursor-pointer">
            <Settings className="mr-2 h-4 w-4" />
            Settings
          </DropdownMenuItem>
          <DropdownMenuItem className="text-xs hover:cursor-pointer">
            <KeyRound className="mr-2 h-4 w-4" />
            <span>Credentials</span>
          </DropdownMenuItem>
          <DropdownMenuItem className="text-xs hover:cursor-pointer">
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
        <Button className="mt-1 w-full text-xs">Join our Discord</Button>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
