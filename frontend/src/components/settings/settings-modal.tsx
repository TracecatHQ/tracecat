"use client"

import { LogOut, UserIcon } from "lucide-react"
import { useRef, useState } from "react"
import { CopyButton } from "@/components/copy-button"
import { useSettingsModal } from "@/components/settings/settings-modal-context"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Separator } from "@/components/ui/separator"
import { TooltipProvider } from "@/components/ui/tooltip"
import UserAvatar from "@/components/user-avatar"
import { userDefaults } from "@/config/user"
import { useAuth, useAuthActions } from "@/hooks/use-auth"
import { useUserManager } from "@/lib/hooks"

export function SettingsModal() {
  const { open, setOpen } = useSettingsModal()
  const { user } = useAuth()
  const { logout } = useAuthActions()
  const { updateCurrentUser } = useUserManager()
  const displayName = user ? user.getDisplayName() : userDefaults.name
  const [name, setName] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  // Use local state if edited, otherwise fall back to user display name
  const currentName = name ?? displayName

  function handleNameBlur() {
    const trimmed = (name ?? "").trim()
    if (trimmed && trimmed !== displayName) {
      // Split into first/last name
      const parts = trimmed.split(/\s+/)
      const firstName = parts[0] || null
      const lastName = parts.slice(1).join(" ") || null
      updateCurrentUser({ first_name: firstName, last_name: lastName })
    }
    // Reset local state so it tracks the server value
    setName(null)
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      inputRef.current?.blur()
    }
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="max-w-[900px] h-[600px] p-0 gap-0 overflow-hidden">
        <TooltipProvider>
          <DialogTitle className="sr-only">Settings</DialogTitle>
          <DialogDescription className="sr-only">
            Manage your account settings and profile
          </DialogDescription>
          <div className="flex h-full">
            {/* Left nav panel */}
            <div className="flex w-[200px] shrink-0 flex-col border-r">
              <div className="flex flex-col gap-1 p-3">
                <span className="px-2 py-1 text-xs font-medium text-muted-foreground">
                  Account
                </span>
                <button
                  type="button"
                  className="flex items-center gap-2 rounded-md bg-muted px-2 py-1.5 text-sm font-medium"
                >
                  <UserIcon className="size-4" />
                  Profile
                </button>
              </div>
              <div className="mt-auto p-3">
                <Separator className="mb-3" />
                <button
                  type="button"
                  className="flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-sm text-muted-foreground hover:bg-muted hover:text-foreground"
                  onClick={() => {
                    setOpen(false)
                    logout()
                  }}
                >
                  <LogOut className="size-4" />
                  Sign out
                </button>
              </div>
            </div>

            {/* Right content panel */}
            <div className="flex flex-1 flex-col gap-6 overflow-y-auto p-8">
              <div className="grid gap-4">
                <div className="grid gap-4">
                  <div className="grid gap-2">
                    <UserAvatar
                      email={user?.email ?? userDefaults.email}
                      firstName={user?.firstName}
                      alt={displayName}
                      className="size-20 rounded-full text-4xl"
                      fallbackClassName="text-3xl"
                    />
                  </div>
                  <div className="grid gap-1.5">
                    <label
                      htmlFor="settings-name"
                      className="text-sm font-medium text-muted-foreground"
                    >
                      Name
                    </label>
                    <Input
                      ref={inputRef}
                      id="settings-name"
                      value={currentName}
                      onChange={(e) => setName(e.target.value)}
                      onBlur={handleNameBlur}
                      onKeyDown={handleKeyDown}
                      className="max-w-lg"
                    />
                  </div>
                </div>

                <div className="grid gap-1.5">
                  <span className="text-sm font-medium text-muted-foreground">
                    Email
                  </span>
                  <span className="text-sm">
                    {user?.email ?? userDefaults.email}
                  </span>
                </div>

                <div className="grid gap-1.5">
                  <span className="text-sm font-medium text-muted-foreground">
                    User ID
                  </span>
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-mono">{user?.id ?? "—"}</span>
                    {user?.id && (
                      <CopyButton
                        value={user.id}
                        toastMessage="User ID copied"
                      />
                    )}
                  </div>
                </div>
              </div>
            </div>
          </div>
        </TooltipProvider>
      </DialogContent>
    </Dialog>
  )
}
