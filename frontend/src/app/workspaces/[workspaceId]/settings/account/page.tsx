"use client"

import { useRouter } from "next/navigation"
import { useAuth } from "@/providers/auth"

import { Separator } from "@/components/ui/separator"
import { ResetPasswordForm } from "@/components/auth/reset-password-form"
import { UpdateEmailForm } from "@/components/auth/update-email-form"
import { UserDetails } from "@/components/auth/user-details-table"

export default function AccountSettingsPage() {
  const { user } = useAuth()
  const router = useRouter()
  if (!user) {
    return router.push("/sign-in")
  }
  return (
    <div className="h-full space-y-6">
      <div className="flex items-end justify-between">
        <h3 className="text-lg font-semibold">Account</h3>
      </div>
      <Separator />
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
          <h6 className="font-bold">Reset Password</h6>
          <div className="flex items-center justify-between">
            <div className="text-muted-foreground">
              <ResetPasswordForm user={user} />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
