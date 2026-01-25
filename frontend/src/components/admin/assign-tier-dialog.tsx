"use client"

import { useState } from "react"
import type { TierRead } from "@/client"
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
import { useAdminOrgTier, useAdminTiers } from "@/hooks/use-admin"

interface AssignTierDialogProps {
  orgId: string
  currentTierId?: string | null
  trigger?: React.ReactNode
}

export function AssignTierDialog({
  orgId,
  currentTierId,
  trigger,
}: AssignTierDialogProps) {
  const [open, setOpen] = useState(false)
  const [selectedTierId, setSelectedTierId] = useState<string | null>(
    currentTierId ?? null
  )
  const { tiers } = useAdminTiers()
  const { updateOrgTier, updatePending } = useAdminOrgTier(orgId)

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

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        {trigger ?? <Button size="sm">Assign tier</Button>}
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Assign tier</DialogTitle>
          <DialogDescription>
            Select a tier to assign to this organization. The tier determines
            resource limits and available features.
          </DialogDescription>
        </DialogHeader>
        <div className="py-4">
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
        <DialogFooter>
          <Button variant="outline" onClick={() => setOpen(false)}>
            Cancel
          </Button>
          <Button
            onClick={handleAssign}
            disabled={updatePending || selectedTierId === currentTierId}
          >
            {updatePending ? "Assigning..." : "Assign"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
