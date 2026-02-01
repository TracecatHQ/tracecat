"use client"

import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { useAuth } from "@/hooks/use-auth"

export default function ProfileSettingsPage() {
  const { user, userIsLoading } = useAuth()

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
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
