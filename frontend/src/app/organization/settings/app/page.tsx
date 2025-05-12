"use client"

import { useAppInfo } from "@/lib/hooks"
import { Badge } from "@/components/ui/badge"
import { Label } from "@/components/ui/label"
import { OrgSettingsAppForm } from "@/components/organization/org-settings-app"

export default function AppSettingsPage() {
  const { appInfo } = useAppInfo()
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Application settings
            </h2>
            <p className="text-md text-muted-foreground">
              View and manage your organization application settings here.
            </p>
          </div>
        </div>

        <div className="mb-4 flex flex-row items-center justify-between rounded-lg border p-4">
          <div className="space-y-0.5">
            <Label>Application version</Label>
          </div>
          <Badge variant="secondary" className="text-xs text-muted-foreground">
            {appInfo?.version ?? "Unknown"}
          </Badge>
        </div>
        <OrgSettingsAppForm />
      </div>
    </div>
  )
}
