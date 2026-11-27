"use client"

import { ArrowUpRight } from "lucide-react"
import { ScopeGuard } from "@/components/auth/scope-guard"
import { EntitlementRequiredEmptyState } from "@/components/entitlement-required-empty-state"
import { CenteredSpinner } from "@/components/loading/spinner"
import { OrgSettingsBrandingForm } from "@/components/organization/org-settings-branding"
import { Button } from "@/components/ui/button"
import { useEntitlements } from "@/hooks/use-entitlements"

export default function BrandingSettingsPage() {
  const { hasEntitlement, isLoading } = useEntitlements()

  if (isLoading) {
    return <CenteredSpinner />
  }

  return (
    <ScopeGuard
      scope="org:settings:read"
      fallback={<p>You do not have permission to view branding settings.</p>}
    >
      <div className="size-full overflow-auto">
        <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
          <div className="flex w-full">
            <div className="items-start space-y-3 text-left">
              <h2 className="text-2xl font-semibold tracking-tight">
                Branding
              </h2>
              <p className="text-base text-muted-foreground">
                Set your organization's logo, primary colors, and sign-in page
                notice.
              </p>
            </div>
          </div>
          {hasEntitlement("custom_branding") ? (
            <OrgSettingsBrandingForm />
          ) : (
            <div className="flex flex-1 items-center justify-center pb-8">
              <EntitlementRequiredEmptyState
                title="Upgrade required"
                description="Custom branding is unavailable on your current plan."
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
    </ScopeGuard>
  )
}
