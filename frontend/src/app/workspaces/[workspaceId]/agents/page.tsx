"use client"

import { ArrowUpRight } from "lucide-react"
import { Suspense } from "react"
import { AgentsDashboard } from "@/components/agents/agents-dashboard"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { Button } from "@/components/ui/button"
import { useEntitlements } from "@/hooks/use-entitlements"

export default function AgentsPage() {
  const { hasEntitlement, isLoading } = useEntitlements()
  const agentAddonsEnabled = hasEntitlement("agent_addons")

  if (isLoading) return <CenteredSpinner />

  if (!agentAddonsEnabled) {
    return (
      <div className="size-full overflow-auto">
        <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
          <div className="flex w-full">
            <div className="items-start space-y-3 text-left">
              <h2 className="text-2xl font-semibold tracking-tight">Agents</h2>
              <p className="text-md text-muted-foreground">
                Create and manage AI agent presets for your workspace.
              </p>
            </div>
          </div>
          <div className="flex flex-1 items-center justify-center pb-8">
            <EntitlementRequiredEmptyState
              title="Upgrade required"
              description="Agents are unavailable on your current plan."
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
      </div>
    )
  }

  return (
    <Suspense fallback={<CenteredSpinner />}>
      <AgentsDashboard />
    </Suspense>
  )
}
