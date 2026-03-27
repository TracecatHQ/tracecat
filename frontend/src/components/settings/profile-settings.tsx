"use client"

import { useRef, useState } from "react"
import { CopyButton } from "@/components/copy-button"
import { Input } from "@/components/ui/input"
import UserAvatar from "@/components/user-avatar"
import { userDefaults } from "@/config/user"
import { useAuth } from "@/hooks/use-auth"
import { useUserManager } from "@/lib/hooks"

export interface ProfileNameUpdate {
  first_name: string | null
  last_name: string | null
}

export function getProfileNameUpdate(
  displayName: string,
  draftName: string | null
): ProfileNameUpdate | null {
  const currentName = displayName.trim().replace(/\s+/g, " ")
  const nextName = (draftName ?? displayName).trim().replace(/\s+/g, " ")

  if (nextName === currentName) {
    return null
  }
  if (!nextName) {
    return { first_name: null, last_name: null }
  }

  const parts = nextName.split(/\s+/)
  return {
    first_name: parts[0] || null,
    last_name: parts.slice(1).join(" ") || null,
  }
}

export function ProfileSettings() {
  const { user } = useAuth()
  const { updateCurrentUser } = useUserManager()
  const displayName = user ? user.getDisplayName() : userDefaults.name
  const [name, setName] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)

  const currentName = name ?? displayName

  function handleNameBlur() {
    const update = getProfileNameUpdate(displayName, name)
    if (update) {
      updateCurrentUser(update)
    }
    setName(null)
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      inputRef.current?.blur()
    }
  }

  return (
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
        <span className="text-sm font-medium text-muted-foreground">Email</span>
        <span className="text-sm">{user?.email ?? userDefaults.email}</span>
      </div>

      <div className="grid gap-1.5">
        <span className="text-sm font-medium text-muted-foreground">
          User ID
        </span>
        <div className="flex items-center gap-2">
          <span className="font-mono text-sm">{user?.id ?? "—"}</span>
          {user?.id && (
            <CopyButton value={user.id} toastMessage="User ID copied" />
          )}
        </div>
      </div>
    </div>
  )
}
