"use client"

import { OrgSettingsAgentForm } from "@/components/organization/org-settings-agent"

export default function AgentSettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">AI models</h2>
            <p className="text-base text-muted-foreground">
              Manage the organization-enabled model catalog, org-scoped provider
              credentials, custom sources, and the default model.
            </p>
          </div>
        </div>

        <OrgSettingsAgentForm />
      </div>
    </div>
  )
}
