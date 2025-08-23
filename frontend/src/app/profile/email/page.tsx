"use client"

import { UpdateEmailForm } from "@/components/auth/update-email-form"
import { CenteredSpinner } from "@/components/loading/spinner"
import { AlertNotification } from "@/components/notifications"
import { useAuth } from "@/hooks/use-auth"

export default function EmailPage() {
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
              Email settings
            </h2>
            <p className="text-md text-muted-foreground">
              Update the email address associated with your account.
            </p>
          </div>
        </div>

        <div className="space-y-6">
          <div className="space-y-4">
            <div className="space-y-2">
              <p className="text-sm font-medium text-muted-foreground">
                Current email
              </p>
              <p className="text-sm">{user.email}</p>
            </div>
          </div>

          <div className="space-y-4">
            <UpdateEmailForm user={user} />
          </div>
        </div>
      </div>
    </div>
  )
}
