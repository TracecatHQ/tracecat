"use client"

import { OrgSettingsSchedules } from "@/components/organization/org-settings-schedules"

export default function SchedulesSettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Schedules</h2>
            <p className="text-base text-muted-foreground">
              Inspect Temporal schedule sync and recreate missing schedules for
              this organization.
            </p>
          </div>
        </div>
        <OrgSettingsSchedules />
      </div>
    </div>
  )
}
