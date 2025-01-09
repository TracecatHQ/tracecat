"use client"

import { Separator } from "@/components/ui/separator"
import { OrgSettingsAuthForm } from "@/components/organization/org-settings-auth"

export default function AuthSettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="space-y-8">
          <div className="flex w-full">
            <div className="items-start space-y-3 text-left">
              <h2 className="text-2xl font-semibold tracking-tight">
                Authentication
              </h2>
              <p className="text-md text-muted-foreground">
                View and manage your organization authentication settings here.
              </p>
            </div>
          </div>
          <Separator />
        </div>
        <OrgSettingsAuthForm />
      </div>
    </div>
  )
}
