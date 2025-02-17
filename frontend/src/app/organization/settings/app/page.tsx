"use client"

import { OrgSettingsAppForm } from "@/components/organization/org-settings-app"

export default function AppSettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Application Settings
            </h2>
            <p className="text-md text-muted-foreground">
              View and manage your organization App settings here.
            </p>
          </div>
        </div>

        <OrgSettingsAppForm />
      </div>
    </div>
  )
}
