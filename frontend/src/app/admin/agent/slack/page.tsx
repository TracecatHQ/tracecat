"use client"

import { PlatformSlackAppSettings } from "@/components/admin/platform-slack-app-settings"

export default function AdminSlackAppPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Slack app</h2>
            <p className="text-base text-muted-foreground">
              Register the Slack app every organization adds to Slack in one
              click.
            </p>
          </div>
        </div>

        <PlatformSlackAppSettings />
      </div>
    </div>
  )
}
