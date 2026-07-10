"use client"

import { OrgAgentOtelSettings } from "@/components/organization/org-agent-otel-settings"

export default function AgentOtelSettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Agent OTel
            </h2>
            <p className="text-base text-muted-foreground">
              Configure outbound OpenTelemetry for agent runtime telemetry.
            </p>
          </div>
        </div>

        <OrgAgentOtelSettings />
      </div>
    </div>
  )
}
