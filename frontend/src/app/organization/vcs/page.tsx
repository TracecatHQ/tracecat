"use client"

import { OrgVCSSettings } from "@/components/organization/org-vcs-settings"

export default function VCSSettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Version control system
            </h2>
            <p className="text-md text-muted-foreground">
              Set up and manage integrations with your version control systems.
            </p>
          </div>
        </div>

        <OrgVCSSettings />
      </div>
    </div>
  )
}
