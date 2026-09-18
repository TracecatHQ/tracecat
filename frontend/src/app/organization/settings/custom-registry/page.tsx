"use client"

import { OrgSettingsCustomRegistryForm } from "@/components/organization/org-settings-custom-registry"

export default function CustomRegistrySettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Repository
            </h2>
            <p className="text-base text-muted-foreground">
              Connect the Git repository that hosts your organization's custom
              actions.
            </p>
          </div>
        </div>

        <OrgSettingsCustomRegistryForm />
      </div>
    </div>
  )
}
