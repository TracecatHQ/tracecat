"use client"

import type { ManagedRolloutRead, ManagedTargetRead } from "@/client"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { RolloutPreviewDetails } from "./rollout-preview-details"
import { RolloutStatusBadge } from "./status-badge"

/** Props for {@link RolloutDetailSheet}. */
export interface RolloutDetailSheetProps {
  rollout: ManagedRolloutRead | null
  target: ManagedTargetRead | null
  onOpenChange: (open: boolean) => void
}

/** Side panel with one target's full rollout preview. */
export function RolloutDetailSheet({
  rollout,
  target,
  onOpenChange,
}: RolloutDetailSheetProps) {
  return (
    <Sheet open={rollout !== null} onOpenChange={onOpenChange}>
      <SheetContent className="w-full overflow-y-auto sm:max-w-2xl">
        {rollout && (
          <div className="space-y-6">
            <SheetHeader>
              <SheetTitle>
                {target
                  ? `${target.organization_name} / ${target.workspace_name}`
                  : "Rollout"}
              </SheetTitle>
              <SheetDescription>
                {rollout.tag ? (
                  <>
                    Rollout of <span className="font-mono">{rollout.tag}</span>
                    {rollout.commit_sha &&
                      ` at ${rollout.commit_sha.substring(0, 7)}`}
                  </>
                ) : (
                  "Unassign rollout"
                )}
              </SheetDescription>
            </SheetHeader>
            <div className="flex items-center gap-2">
              <RolloutStatusBadge status={rollout.status} />
            </div>
            {rollout.error && (
              <p className="whitespace-pre-wrap text-xs text-destructive">
                {rollout.error}
              </p>
            )}
            {rollout.preview ? (
              <RolloutPreviewDetails preview={rollout.preview} />
            ) : (
              <p className="text-sm text-muted-foreground">
                {rollout.status === "previewing"
                  ? "Preview is running..."
                  : "No preview available."}
              </p>
            )}
          </div>
        )}
      </SheetContent>
    </Sheet>
  )
}
