"use client"

import { ArrowUpRight } from "lucide-react"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { RegistryRepositoriesTable } from "@/components/registry/registry-repos-table"
import { Button } from "@/components/ui/button"
import { useEntitlements } from "@/hooks/use-entitlements"

export default function RegistryRepositoriesPage() {
  const canReadRegistry = useScopeCheck("org:registry:read")
  const { hasEntitlement, isLoading: entitlementsLoading } = useEntitlements()
  const customRegistryEnabled = hasEntitlement("custom_registry")

  const isLoading = canReadRegistry === undefined || entitlementsLoading

  if (isLoading) return <CenteredSpinner />
  if (!canReadRegistry) return null

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Repositories
            </h2>
            <p className="text-base text-muted-foreground">
              View your organization&apos;s action repositories here.
            </p>
          </div>
        </div>
        {customRegistryEnabled ? (
          <RegistryRepositoriesTable />
        ) : (
          <div className="flex flex-1 items-center justify-center pb-8">
            <EntitlementRequiredEmptyState
              title="Upgrade required"
              description="Custom registry repositories are unavailable on your current plan."
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
        )}
      </div>
    </div>
  )
}
