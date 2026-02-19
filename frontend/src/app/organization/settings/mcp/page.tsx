"use client"

import { OrgSettingsMCP } from "@/components/organization/org-settings-mcp"

export default function MCPSettingsPage() {
  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              MCP access
            </h2>
            <p className="text-base text-muted-foreground">
              Generate an organization-scoped MCP connection for IDE clients.
            </p>
          </div>
        </div>

        <OrgSettingsMCP />
      </div>
    </div>
  )
}
