import { useSessionContext, useUser } from "@/providers/session"
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
}
export function UserNav() {
  const { signOut } = useSessionContext()
  const user = useUser()
  console.log("user", user)
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" className="relative h-8 w-8 rounded-full">
          <UserAvatar />
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
          <DropdownMenuItem className="hover:cursor-pointer">
            <Settings className="mr-2 h-4 w-4" />
            Settings
          </DropdownMenuItem>
          <DropdownMenuItem className="hover:cursor-pointer">
            <KeyRound className="mr-2 h-4 w-4" />
            <span>Credentials</span>
          </DropdownMenuItem>
          <DropdownMenuItem className="hover:cursor-pointer">
            <UsersRound className="mr-2 h-4 w-4" />
            <span>Manage users</span>
          </DropdownMenuItem>
        </DropdownMenuGroup>
        <DropdownMenuSeparator />
        <DropdownMenuItem className="hover:cursor-pointer" onClick={signOut}>
          <LogOut className="mr-2 h-4 w-4" />
          <span>Logout</span>
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <Button className="mt-1 w-full text-xs">Join our Discord</Button>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
