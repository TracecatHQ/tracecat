"use client"

import { UpdatePasswordForm } from "@/components/auth/update-password-form"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { useAuth } from "@/hooks/use-auth"

export default function SecurityPage() {
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
            <h2 className="text-2xl font-semibold tracking-tight">Security</h2>
            <p className="text-md text-muted-foreground">
              Manage your account security settings.
            </p>
          </div>
        </div>

        <div className="space-y-8">
          <div className="space-y-4">
            <h2 className="text-xl font-semibold">Password</h2>
            <p className="text-sm text-muted-foreground">
              Update your password to keep your account secure.
            </p>
            <UpdatePasswordForm />
          </div>
        </div>
      </div>
    </div>
  )
}
