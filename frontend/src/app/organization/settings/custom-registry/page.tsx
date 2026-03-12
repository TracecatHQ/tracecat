"use client"

import { ArrowUpRight } from "lucide-react"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { OrgSettingsCustomRegistryForm } from "@/components/organization/org-settings-custom-registry"
import { Button } from "@/components/ui/button"
import { useEntitlements } from "@/hooks/use-entitlements"

export default function CustomRegistrySettingsPage() {
  const { hasEntitlement, isLoading } = useEntitlements()
  const customRegistryEnabled = hasEntitlement("custom_registry")

  if (isLoading) return <CenteredSpinner />

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Custom registry
            </h2>
            <p className="text-md text-muted-foreground">
              View and manage your organization's custom registry settings here.
            </p>
          </div>
        </div>

        {customRegistryEnabled ? (
          <OrgSettingsCustomRegistryForm />
        ) : (
          <div className="flex flex-1 items-center justify-center pb-8">
            <EntitlementRequiredEmptyState
              title="Upgrade required"
              description="Custom registry Git settings are unavailable on your current plan."
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
