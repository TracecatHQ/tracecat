"use client"

import { ArrowLeftIcon } from "lucide-react"
import Link from "next/link"
import { use } from "react"
import { AssignTierDialog } from "@/components/admin/assign-tier-dialog"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { useAdminOrganization, useAdminOrgTier } from "@/hooks/use-admin"

export default function AdminOrgTierPage({
  params,
}: {
  params: Promise<{ orgId: string }>
}) {
  const { orgId } = use(params)
  const { organization } = useAdminOrganization(orgId)
  const { orgTier, isLoading } = useAdminOrgTier(orgId)

  if (isLoading) {
    return <div className="text-center text-muted-foreground">Loading...</div>
  }

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div>
          <Link
            href="/admin/organizations"
            className="flex items-center text-sm text-muted-foreground hover:text-foreground mb-4"
          >
            <ArrowLeftIcon className="mr-2 size-4" />
            Back to organizations
          </Link>
          <div className="flex w-full">
            <div className="items-start space-y-3 text-left">
              <h2 className="text-2xl font-semibold tracking-tight">
                Tier assignment
              </h2>
              <p className="text-md text-muted-foreground">
                Manage tier assignment for{" "}
                {organization?.name ?? "organization"}.
              </p>
            </div>
          </div>
        </div>

        <Card>
          <CardHeader>
            <CardTitle className="flex items-center justify-between">
              <span>Current tier</span>
              <AssignTierDialog
                orgId={orgId}
                currentTierId={orgTier?.tier_id}
                trigger={<Button size="sm">Change tier</Button>}
              />
            </CardTitle>
            <CardDescription>
              The tier determines resource limits and available features for
              this organization.
            </CardDescription>
          </CardHeader>
          <CardContent>
            {orgTier?.tier ? (
              <div className="space-y-4">
                <div className="flex items-center gap-2">
                  <span className="font-medium">
                    {orgTier.tier.display_name}
                  </span>
                  {orgTier.tier.is_default && (
                    <Badge variant="secondary">Default</Badge>
                  )}
                </div>
                <div className="grid grid-cols-2 gap-4 text-sm">
                  <div>
                    <span className="text-muted-foreground">
                      Max concurrent workflows:
                    </span>
                    <span className="ml-2">
                      {orgTier.max_concurrent_workflows ??
                        orgTier.tier.max_concurrent_workflows ??
                        "Unlimited"}
                    </span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">
                      Max actions per workflow:
                    </span>
                    <span className="ml-2">
                      {orgTier.max_action_executions_per_workflow ??
                        orgTier.tier.max_action_executions_per_workflow ??
                        "Unlimited"}
                    </span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">
                      Max concurrent actions:
                    </span>
                    <span className="ml-2">
                      {orgTier.max_concurrent_actions ??
                        orgTier.tier.max_concurrent_actions ??
                        "Unlimited"}
                    </span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">
                      API rate limit:
                    </span>
                    <span className="ml-2">
                      {orgTier.api_rate_limit ??
                        orgTier.tier.api_rate_limit ??
                        "Unlimited"}
                    </span>
                  </div>
                </div>
              </div>
            ) : (
              <div className="text-muted-foreground">
                No tier assigned. Using default limits.
              </div>
            )}
          </CardContent>
        </Card>

        {orgTier && (
          <Card>
            <CardHeader>
              <CardTitle>Overrides</CardTitle>
              <CardDescription>
                Custom limits that override the base tier settings for this
                organization.
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="text-sm text-muted-foreground">
                {orgTier.max_concurrent_workflows != null ||
                orgTier.max_action_executions_per_workflow != null ||
                orgTier.max_concurrent_actions != null ||
                orgTier.api_rate_limit != null ? (
                  <div className="grid grid-cols-2 gap-4">
                    {orgTier.max_concurrent_workflows != null && (
                      <div>
                        <span className="text-foreground">
                          Max concurrent workflows:
                        </span>
                        <span className="ml-2">
                          {orgTier.max_concurrent_workflows}
                        </span>
                      </div>
                    )}
                    {orgTier.max_action_executions_per_workflow != null && (
                      <div>
                        <span className="text-foreground">
                          Max actions per workflow:
                        </span>
                        <span className="ml-2">
                          {orgTier.max_action_executions_per_workflow}
                        </span>
                      </div>
                    )}
                    {orgTier.max_concurrent_actions != null && (
                      <div>
                        <span className="text-foreground">
                          Max concurrent actions:
                        </span>
                        <span className="ml-2">
                          {orgTier.max_concurrent_actions}
                        </span>
                      </div>
                    )}
                    {orgTier.api_rate_limit != null && (
                      <div>
                        <span className="text-foreground">API rate limit:</span>
                        <span className="ml-2">{orgTier.api_rate_limit}</span>
                      </div>
                    )}
                  </div>
                ) : (
                  "No overrides configured."
                )}
              </div>
            </CardContent>
          </Card>
        )}
      </div>
    </div>
  )
}
