"use client"

import { OrgSettingsAuthForm } from "@/components/organization/org-settings-auth"

export default function AuthSettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Email authentication
            </h2>
            <p className="text-md text-muted-foreground">
              View and manage your organization email authentication settings
              here.
            </p>
          </div>
        </div>

        <OrgSettingsAuthForm />
      </div>
    </div>
  )
}
