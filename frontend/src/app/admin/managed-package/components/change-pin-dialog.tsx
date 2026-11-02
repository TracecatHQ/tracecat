"use client"

import { useState } from "react"
import type { GitTagInfo, ManagedTargetRead } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { toast } from "@/components/ui/use-toast"
import { tagAge, toastApiError } from "../managed-package-utils"
import { useUpdateTargetPin } from "../use-managed-package"

/** Props for {@link ChangePinDialog}. */
export interface ChangePinDialogProps {
  target: ManagedTargetRead | null
  tags: GitTagInfo[]
  onOpenChange: (open: boolean) => void
}

/** Move a target's pinned tag. A rollout is still needed to apply it. */
export function ChangePinDialog({
  target,
  tags,
  onOpenChange,
}: ChangePinDialogProps) {
  return (
    <Dialog open={target !== null} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        {target && (
          <ChangePinForm
            target={target}
            tags={tags}
            onDone={() => onOpenChange(false)}
          />
        )}
      </DialogContent>
    </Dialog>
  )
}

function ChangePinForm({
  target,
  tags,
  onDone,
}: {
  target: ManagedTargetRead
  tags: GitTagInfo[]
  onDone: () => void
}) {
  const [pinnedTag, setPinnedTag] = useState(target.pinned_tag)
  const updatePin = useUpdateTargetPin()
  const isRollback =
    tagAge(tags, pinnedTag) > tagAge(tags, target.pinned_tag) &&
    tagAge(tags, target.pinned_tag) >= 0

  async function handleSave() {
    try {
      await updatePin.mutateAsync({ targetId: target.id, pinnedTag })
      toast({
        title: "Pin updated",
        description: `${target.workspace_name} is pinned to ${pinnedTag}. Preview and apply to roll it out.`,
      })
      onDone()
    } catch (err) {
      toastApiError("Failed to update pin", err)
    }
  }

  return (
    <>
      <DialogHeader>
        <DialogTitle>Change pin</DialogTitle>
        <DialogDescription>
          {target.organization_name} / {target.workspace_name}
        </DialogDescription>
      </DialogHeader>
      <DialogBody>
        <div className="space-y-2">
          <Label>Pinned tag</Label>
          <Select value={pinnedTag} onValueChange={setPinnedTag}>
            <SelectTrigger>
              <SelectValue placeholder="Select a tag" />
            </SelectTrigger>
            <SelectContent>
              {tags.map((tag) => (
                <SelectItem key={tag.name} value={tag.name}>
                  <span className="font-mono">{tag.name}</span>
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          {isRollback && (
            <p className="text-xs text-amber-600 dark:text-amber-400">
              This tag is older than the current pin. Applying it rolls the
              target back.
            </p>
          )}
          <p className="text-xs text-muted-foreground">
            Changing the pin doesn&apos;t apply anything. Preview and apply a
            rollout to update the workspace.
          </p>
        </div>
      </DialogBody>
      <DialogFooter>
        <Button
          size="sm"
          onClick={handleSave}
          disabled={updatePin.isPending || pinnedTag === target.pinned_tag}
        >
          {updatePin.isPending ? "Saving..." : "Save"}
        </Button>
      </DialogFooter>
    </>
  )
}
