"use client"

import { useEffect, useMemo, useState } from "react"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { Button } from "@/components/ui/button"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useAuth } from "@/hooks/use-auth"
import { useUserManager, useWorkspaceManager } from "@/lib/hooks"
import {
  getDefaultWorkspacePreference,
  withDefaultWorkspacePreference,
} from "@/lib/user-settings"

const LAST_VIEWED_WORKSPACE_OPTION = "__last_viewed__"

export default function ProfileSettingsPage() {
  const { user, userIsLoading } = useAuth()
  const { workspaces, workspacesLoading } = useWorkspaceManager()
  const { updateCurrentUser, updateCurrentUserPending } = useUserManager()

  const defaultWorkspacePreference = useMemo(
    () => getDefaultWorkspacePreference(user?.settings),
    [user?.settings]
  )

  const initialDefaultWorkspace = useMemo(() => {
    if (defaultWorkspacePreference.strategy === "specific") {
      return (
        defaultWorkspacePreference.workspaceId ?? LAST_VIEWED_WORKSPACE_OPTION
      )
    }
    return LAST_VIEWED_WORKSPACE_OPTION
  }, [defaultWorkspacePreference])

  const [selectedDefaultWorkspace, setSelectedDefaultWorkspace] = useState(
    initialDefaultWorkspace
  )

  useEffect(() => {
    setSelectedDefaultWorkspace(initialDefaultWorkspace)
  }, [initialDefaultWorkspace])

  useEffect(() => {
    if (
      selectedDefaultWorkspace !== LAST_VIEWED_WORKSPACE_OPTION &&
      workspaces &&
      !workspaces.some((workspace) => workspace.id === selectedDefaultWorkspace)
    ) {
      setSelectedDefaultWorkspace(LAST_VIEWED_WORKSPACE_OPTION)
    }
  }, [selectedDefaultWorkspace, workspaces])

  const hasChanges = selectedDefaultWorkspace !== initialDefaultWorkspace

  const selectedWorkspaceUnavailable =
    selectedDefaultWorkspace !== LAST_VIEWED_WORKSPACE_OPTION &&
    !!workspaces &&
    !workspaces.some((workspace) => workspace.id === selectedDefaultWorkspace)

  const isSavingDisabled =
    !hasChanges || updateCurrentUserPending || selectedWorkspaceUnavailable

  const handleSave = async () => {
    if (!user || !hasChanges || selectedWorkspaceUnavailable) {
      return
    }

    const preference =
      selectedDefaultWorkspace === LAST_VIEWED_WORKSPACE_OPTION
        ? { strategy: "last_viewed" as const }
        : {
            strategy: "specific" as const,
            workspaceId: selectedDefaultWorkspace,
          }

    try {
      await updateCurrentUser({
        settings: withDefaultWorkspacePreference(user.settings, preference),
      })
    } catch (error) {
      console.error("Failed to update default workspace preference", error)
    }
  }

  if (userIsLoading) {
    return <CenteredSpinner />
  }

  if (!user) {
    return <AlertNotification level="error" message="User not found" />
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12 py-16">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Profile settings
            </h2>
            <p className="text-md text-muted-foreground">
              Manage your account settings and preferences.
            </p>
          </div>
        </div>

        <div className="space-y-6">
          <div className="space-y-4">
            <h3 className="text-lg font-medium">Account information</h3>
            <div className="space-y-4">
              <div>
                <p className="text-sm font-medium text-muted-foreground">
                  Display name
                </p>
                <p className="text-sm">{user.getDisplayName()}</p>
              </div>
              <div>
                <p className="text-sm font-medium text-muted-foreground">
                  Email address
                </p>
                <p className="text-sm">{user.email}</p>
              </div>
              {user.isPrivileged() && (
                <div>
                  <p className="text-sm font-medium text-muted-foreground">
                    Organization role
                  </p>
                  <p className="text-sm capitalize">{user.role}</p>
                </div>
              )}
            </div>
          </div>

          <div className="space-y-4">
            <h3 className="text-lg font-medium">Workspace preferences</h3>
            <div className="space-y-4">
              <div className="grid gap-3 sm:max-w-sm">
                <Label htmlFor="default-workspace">Default workspace</Label>
                <Select
                  value={selectedDefaultWorkspace}
                  onValueChange={setSelectedDefaultWorkspace}
                  disabled={workspacesLoading || updateCurrentUserPending}
                >
                  <SelectTrigger id="default-workspace">
                    <SelectValue placeholder="Select a workspace" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value={LAST_VIEWED_WORKSPACE_OPTION}>
                      Last viewed workspace
                    </SelectItem>
                    {selectedWorkspaceUnavailable && (
                      <SelectItem value={selectedDefaultWorkspace} disabled>
                        Workspace unavailable
                      </SelectItem>
                    )}
                    {workspaces?.map((workspace) => (
                      <SelectItem key={workspace.id} value={workspace.id}>
                        {workspace.name}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
                <p className="text-sm text-muted-foreground">
                  Choose which workspace opens first when you visit Tracecat.
                </p>
                {selectedWorkspaceUnavailable && (
                  <p className="text-sm text-destructive">
                    The previously selected workspace is no longer available.
                    Please choose another workspace.
                  </p>
                )}
              </div>
              <div>
                <Button onClick={handleSave} disabled={isSavingDisabled}>
                  Save changes
                </Button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
