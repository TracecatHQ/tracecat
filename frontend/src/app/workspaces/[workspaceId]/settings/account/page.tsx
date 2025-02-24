"use client"

import { useRouter } from "next/navigation"
import { useAuth } from "@/providers/auth"

import { Separator } from "@/components/ui/separator"
import { UpdateEmailForm } from "@/components/auth/update-email-form"
import { UpdatePasswordForm } from "@/components/auth/update-password-form"
import { UserDetails } from "@/components/auth/user-details-table"

export default function AccountSettingsPage() {
  const { user } = useAuth()
  const router = useRouter()
  if (!user) {
    return router.push("/sign-in")
  }
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Account</h2>
            <p className="text-md text-muted-foreground">
              Manage your account settings and preferences.
            </p>
          </div>
        </div>

        <div className="space-y-8">
          <div className="space-y-2 text-sm">
            <h6 className="font-bold">Settings</h6>
            <div className="flex items-center justify-between">
              <div className="text-muted-foreground">
                <UserDetails user={user} />
              </div>
            </div>
          </div>

          <div className="space-y-2 text-sm">
            <h6 className="font-bold">Update Email</h6>
            <div className="flex items-center justify-between">
              <UpdateEmailForm user={user} />
            </div>
          </div>
          <div className="space-y-2 text-sm">
            <h6 className="font-bold">Update Password</h6>
            <div className="flex items-center justify-between">
              <div className="text-muted-foreground">
                <UpdatePasswordForm />
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
