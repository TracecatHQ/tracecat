"use client"

import { useRouter } from "next/navigation"
import { UpdatePasswordForm } from "@/components/auth/update-password-form"
import { useAuth } from "@/providers/auth"

export default function SecuritySettingsPage() {
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
            <h2 className="text-2xl font-semibold tracking-tight">Security</h2>
            <p className="text-md text-muted-foreground">
              Manage your account security settings.
            </p>
          </div>
        </div>

        <div className="space-y-8">
          <div>
            <div className="mt-2">
              <UpdatePasswordForm />
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
