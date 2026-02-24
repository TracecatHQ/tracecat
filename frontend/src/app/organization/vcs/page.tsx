"use client"

import { ArrowUpRight } from "lucide-react"

import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { OrgVCSSettings } from "@/components/organization/org-vcs-settings"
import { Button } from "@/components/ui/button"
import { useEntitlements } from "@/hooks/use-entitlements"

export default function VCSSettingsPage() {
  const { hasEntitlement, isLoading } = useEntitlements()

  // Show loading while feature flags are being fetched
  if (isLoading) {
    return <CenteredSpinner />
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Workflow sync
            </h2>
            <p className="text-md text-muted-foreground">
              Sync workflows to and from your private Git repository.
            </p>
          </div>
        </div>

        {hasEntitlement("git_sync") ? (
          <OrgVCSSettings />
        ) : (
          <div className="flex flex-1 items-center justify-center pb-8">
            <EntitlementRequiredEmptyState
              title="Upgrade required"
              description="Workflow sync is unavailable on your current plan."
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
