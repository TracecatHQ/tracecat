"use client"

import { OrgSettingsAgentForm } from "@/components/organization/org-settings-agent"
import { useEntitlements } from "@/hooks/use-entitlements"

export default function AgentSettingsPage() {
  const { hasEntitlement } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">AI models</h2>
            <p className="text-base text-muted-foreground">
              {agentAddonsEnabled
                ? "Manage org-scoped provider credentials, custom sources, allowed models, and the default model."
                : "Manage org-scoped provider credentials and the organization model catalog."}
            </p>
          </div>
        </div>

        <OrgSettingsAgentForm />
      </div>
    </div>
  )
}
