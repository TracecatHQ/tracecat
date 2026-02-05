"use client"

import { useEffect, useState } from "react"
import type { TierRead } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Separator } from "@/components/ui/separator"
import { toast } from "@/components/ui/use-toast"
import {
  useAdminOrganization,
  useAdminOrgTier,
  useAdminTiers,
} from "@/hooks/use-admin"

interface AdminOrgTierDialogProps {
  orgId: string
  trigger: React.ReactNode
}

export function AdminOrgTierDialog({ orgId, trigger }: AdminOrgTierDialogProps) {
  const [open, setOpen] = useState(false)
  const [selectedTierId, setSelectedTierId] = useState<string | null>(null)
  const { organization } = useAdminOrganization(orgId)
  const { orgTier, isLoading, updateOrgTier, updatePending } =
    useAdminOrgTier(orgId)
  const { tiers } = useAdminTiers()

  useEffect(() => {
    if (open) {
      setSelectedTierId(orgTier?.tier_id ?? null)
    }
  }, [open, orgTier?.tier_id])

  const handleAssign = async () => {
    try {
      await updateOrgTier({ tier_id: selectedTierId })
      toast({
        title: "Tier assigned",
        description: "Organization tier has been updated.",
      })
      setOpen(false)
    } catch (error) {
      console.error("Failed to assign tier", error)
      toast({
        title: "Failed to assign tier",
        description: "Please try again.",
        variant: "destructive",
      })
    }
  }

  const currentTier = orgTier?.tier
  const hasOverrides =
    orgTier?.max_concurrent_workflows != null ||
    orgTier?.max_action_executions_per_workflow != null ||
    orgTier?.max_concurrent_actions != null ||
    orgTier?.api_rate_limit != null

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>{trigger}</DialogTrigger>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Tier assignment</DialogTitle>
          <DialogDescription>
            Manage tier assignment for{" "}
            {organization?.name ?? "organization"}.
          </DialogDescription>
        </DialogHeader>
        {isLoading ? (
          <div className="py-8 text-center text-muted-foreground">
            Loading...
          </div>
        ) : (
          <div className="space-y-6">
            <div className="space-y-3">
              <div>
                <h4 className="text-sm font-medium">Current tier</h4>
                <p className="text-xs text-muted-foreground">
                  The tier determines resource limits and available features.
                </p>
              </div>
              {currentTier ? (
                <div className="space-y-4">
                  <div className="flex items-center gap-2">
                    <span className="font-medium">
                      {currentTier.display_name}
                    </span>
                    {currentTier.is_default && (
                      <Badge variant="secondary">Default</Badge>
                    )}
                  </div>
                  <div className="grid grid-cols-2 gap-4 text-sm">
                    <div>
                      <span className="text-muted-foreground">
                        Max concurrent workflows:
                      </span>
                      <span className="ml-2">
                        {orgTier?.max_concurrent_workflows ??
                          currentTier.max_concurrent_workflows ??
                          "Unlimited"}
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">
                        Max actions per workflow:
                      </span>
                      <span className="ml-2">
                        {orgTier?.max_action_executions_per_workflow ??
                          currentTier.max_action_executions_per_workflow ??
                          "Unlimited"}
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">
                        Max concurrent actions:
                      </span>
                      <span className="ml-2">
                        {orgTier?.max_concurrent_actions ??
                          currentTier.max_concurrent_actions ??
                          "Unlimited"}
                      </span>
                    </div>
                    <div>
                      <span className="text-muted-foreground">
                        API rate limit:
                      </span>
                      <span className="ml-2">
                        {orgTier?.api_rate_limit ??
                          currentTier.api_rate_limit ??
                          "Unlimited"}
                      </span>
                    </div>
                  </div>
                </div>
              ) : (
                <div className="text-sm text-muted-foreground">
                  No tier assigned. Using default limits.
                </div>
              )}
            </div>
            <Separator />
            <div className="space-y-3">
              <div>
                <h4 className="text-sm font-medium">Assign tier</h4>
                <p className="text-xs text-muted-foreground">
                  Select a tier to apply to this organization.
                </p>
              </div>
              <Select
                value={selectedTierId ?? undefined}
                onValueChange={setSelectedTierId}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select a tier" />
                </SelectTrigger>
                <SelectContent>
                  {tiers?.map((tier: TierRead) => (
                    <SelectItem key={tier.id} value={tier.id}>
                      <div className="flex items-center gap-2">
                        <span>{tier.display_name}</span>
                        {tier.is_default && (
                          <span className="text-xs text-muted-foreground">
                            (Default)
                          </span>
                        )}
                      </div>
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {orgTier && (
              <>
                <Separator />
                <div className="space-y-3">
                  <div>
                    <h4 className="text-sm font-medium">Overrides</h4>
                    <p className="text-xs text-muted-foreground">
                      Custom limits that override base tier settings.
                    </p>
                  </div>
                  {hasOverrides ? (
                    <div className="grid grid-cols-2 gap-4 text-sm">
                      {orgTier.max_concurrent_workflows != null && (
                        <div>
                          <span className="text-muted-foreground">
                            Max concurrent workflows:
                          </span>
                          <span className="ml-2">
                            {orgTier.max_concurrent_workflows}
                          </span>
                        </div>
                      )}
                      {orgTier.max_action_executions_per_workflow != null && (
                        <div>
                          <span className="text-muted-foreground">
                            Max actions per workflow:
                          </span>
                          <span className="ml-2">
                            {orgTier.max_action_executions_per_workflow}
                          </span>
                        </div>
                      )}
                      {orgTier.max_concurrent_actions != null && (
                        <div>
                          <span className="text-muted-foreground">
                            Max concurrent actions:
                          </span>
                          <span className="ml-2">
                            {orgTier.max_concurrent_actions}
                          </span>
                        </div>
                      )}
                      {orgTier.api_rate_limit != null && (
                        <div>
                          <span className="text-muted-foreground">
                            API rate limit:
                          </span>
                          <span className="ml-2">{orgTier.api_rate_limit}</span>
                        </div>
                      )}
                    </div>
                  ) : (
                    <div className="text-sm text-muted-foreground">
                      No overrides configured.
                    </div>
                  )}
                </div>
              </>
            )}
          </div>
        )}
        <DialogFooter>
          <Button type="button" variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleAssign}
            disabled={
              updatePending ||
              selectedTierId == null ||
              selectedTierId === orgTier?.tier_id
            }
          >
            {updatePending ? "Saving..." : "Save changes"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
