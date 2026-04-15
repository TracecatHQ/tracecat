"use client"

import { ArrowUpRight } from "lucide-react"
import { AgentTagsView } from "@/components/agents/agent-tags-view"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import { useEntitlements } from "@/hooks/use-entitlements"

export default function AgentTagsPage() {
  const { hasEntitlement, isLoading } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")

  if (isLoading) return <CenteredSpinner />

  if (!agentAddonsEnabled) {
    return (
      <div className="size-full overflow-auto">
        <div className="container flex h-full max-w-[1000px] items-center justify-center py-8">
          <EntitlementRequiredEmptyState
            title="Enterprise only"
            description="Agent tags are only available on enterprise plans."
          >
            <Button
              variant="link"
              asChild
              className="text-muted-foreground"
              size="sm"
            >
              <a
                href="https://tracecat.com"
                target="_blank"
                rel="noopener noreferrer"
              >
                Learn more <ArrowUpRight className="size-4" />
              </a>
            </Button>
          </EntitlementRequiredEmptyState>
        </div>
      </div>
    )
  }

  return <AgentTagsView />
}
