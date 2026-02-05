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
import { toast } from "@/components/ui/use-toast"
import {
  useAdminOrganization,
  useAdminOrgTier,
  useAdminTiers,
} from "@/hooks/use-admin"

interface AdminOrgTierDialogProps {
  orgId: string
  trigger?: React.ReactNode
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export function AdminOrgTierDialog({
  orgId,
  trigger,
  open: controlledOpen,
  onOpenChange,
}: AdminOrgTierDialogProps) {
  const [internalOpen, setInternalOpen] = useState(false)
  const isControlled = controlledOpen !== undefined
  const dialogOpen = isControlled ? controlledOpen : internalOpen
  const setDialogOpen = (nextOpen: boolean) => {
    if (!isControlled) {
      setInternalOpen(nextOpen)
    }
    onOpenChange?.(nextOpen)
  }
  const [selectedTierId, setSelectedTierId] = useState<string | null>(null)
  const { organization } = useAdminOrganization(orgId)
  const { orgTier, isLoading, updateOrgTier, updatePending } =
    useAdminOrgTier(orgId)
  const { tiers } = useAdminTiers()

  useEffect(() => {
    if (dialogOpen) {
      setSelectedTierId(orgTier?.tier_id ?? null)
    }
  }, [dialogOpen, orgTier?.tier_id])

  const handleAssign = async () => {
    try {
      await updateOrgTier({ tier_id: selectedTierId })
      toast({
        title: "Tier assigned",
        description: "Organization tier has been updated.",
      })
      setDialogOpen(false)
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
    <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
      {trigger ? <DialogTrigger asChild>{trigger}</DialogTrigger> : null}
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Tier assignment</DialogTitle>
          <DialogDescription>
            Manage tier assignment for {organization?.name ?? "organization"}.
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
              </div>
              {currentTier ? (
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium">
                    {currentTier.display_name}
                  </span>
                  {currentTier.is_default && (
                    <Badge variant="secondary">Platform default</Badge>
                  )}
                </div>
              ) : (
                <div className="text-sm text-muted-foreground">
                  No tier assigned. Using default limits.
                </div>
              )}
            </div>
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
                            (Platform default)
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
          <Button
            type="button"
            variant="outline"
            onClick={() => setDialogOpen(false)}
          >
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
