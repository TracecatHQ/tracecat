"use client"

import { OrgAgentOtelSettings } from "@/components/organization/org-agent-otel-settings"

export default function AgentTelemetrySettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">Telemetry</h2>
            <p className="text-base text-muted-foreground">
              Export agent runtime telemetry to an OTel-compatible collector.
            </p>
          </div>
        </div>

        <OrgAgentOtelSettings />
      </div>
    </div>
  )
}
