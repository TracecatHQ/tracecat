"use client"

import { PlatformRegistrySettings } from "@/components/admin/platform-registry-settings"

export default function AdminRegistrySettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Settings</h2>
            <p className="text-base text-muted-foreground">
              Configure platform registry settings.
            </p>
          </div>
        </div>

        <PlatformRegistrySettings />
      </div>
    </div>
  )
}
