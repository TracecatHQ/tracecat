"use client"

import { XIcon } from "lucide-react"
import { useState } from "react"
import {
  LockedFeatureChip,
  LockedFeatureModal,
} from "@/components/locked-feature-modal"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { useEntitlements } from "@/hooks/use-entitlements"
import { useTunnels } from "@/hooks/use-tunnels"
import { cn } from "@/lib/utils"
import { useWorkspaceId } from "@/providers/workspace-id"

const NONE_VALUE = "__none__"

/**
 * Picks a workspace tunnel by name. Without the tunnels entitlement it renders
 * a locked control that opens the upgrade dialog and can still clear a saved
 * tunnel.
 */
export function TunnelSelect({
  value,
  onChange,
  noneLabel,
}: {
  value: string | null | undefined
  onChange: (value: string | undefined) => void
  noneLabel: string
}) {
  const workspaceId = useWorkspaceId()
  const [lockedOpen, setLockedOpen] = useState(false)
  const {
    hasEntitlement,
    hasEntitlementData,
    isLoading: entitlementsLoading,
  } = useEntitlements()
  const tunnelsEnabled = hasEntitlement("tunnels")
  const { tunnels, isLoading } = useTunnels(workspaceId, {
    enabled: tunnelsEnabled,
  })

  if (!entitlementsLoading && hasEntitlementData && !tunnelsEnabled) {
    return (
      <>
        <div className="flex items-center gap-1">
          <Button
            type="button"
            variant="outline"
            size="sm"
            className={cn(
              "h-8 flex-1 justify-between text-xs font-normal",
              !value && "text-muted-foreground"
            )}
            onClick={() => setLockedOpen(true)}
          >
            {value || noneLabel}
            <LockedFeatureChip />
          </Button>
          {/* A saved tunnel stays visible after a downgrade so it can be cleared. */}
          {value ? (
            <Button
              type="button"
              variant="ghost"
              size="icon"
              className="size-8 shrink-0"
              aria-label="Clear tunnel"
              onClick={() => onChange(undefined)}
            >
              <XIcon className="size-3.5" />
            </Button>
          ) : null}
        </div>
        <LockedFeatureModal
          open={lockedOpen}
          onOpenChange={setLockedOpen}
          title="Upgrade to use tunnels"
          description="Tunnels let actions reach private hosts in your network through Pangolin."
        />
      </>
    )
  }

  const names = new Set(tunnels.map((tunnel) => tunnel.name))
  // Keep a saved name selectable even if the tunnel was deleted, so the
  // validation error stays visible instead of silently clearing the field.
  const missing = value && !isLoading && !names.has(value) ? value : null

  return (
    <Select
      value={value || NONE_VALUE}
      onValueChange={(next) => onChange(next === NONE_VALUE ? undefined : next)}
      disabled={entitlementsLoading || !hasEntitlementData}
    >
      <SelectTrigger className="h-8 text-xs">
        <SelectValue placeholder={noneLabel} />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={NONE_VALUE} className="text-xs">
          {noneLabel}
        </SelectItem>
        {tunnels.map((tunnel) => (
          <SelectItem key={tunnel.id} value={tunnel.name} className="text-xs">
            {tunnel.name}
            {tunnel.enabled ? "" : " (disabled)"}
          </SelectItem>
        ))}
        {missing ? (
          <SelectItem value={missing} className="text-xs">
            {missing} (not found)
          </SelectItem>
        ) : null}
      </SelectContent>
    </Select>
  )
}
