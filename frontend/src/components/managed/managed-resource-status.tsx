"use client"

import { UnlinkIcon } from "lucide-react"
import { useState } from "react"
import type { DetachedCopy, SyncResourceType } from "@/client"
import { useScopeCheck } from "@/components/auth/scope-guard"
import { DetachDialog } from "@/components/managed/detach-dialog"
import { DetachedNote, ManagedBadge } from "@/components/managed/managed-badge"
import { Button } from "@/components/ui/button"
import { useManagedResources } from "@/hooks/use-managed-resources"
import { cn } from "@/lib/utils"

/** Props for {@link ManagedResourceStatus}. */
export interface ManagedResourceStatusProps {
  resourceType: SyncResourceType
  /** Resource id, or the field name for `case_field`. */
  id?: string | null
  /** Natural name used to prefill the detach dialog. */
  name: string
  /** Show the "Detach to editable copy" button to workspace admins. */
  showDetach?: boolean
  /** Show only the lock icon. */
  compact?: boolean
  onDetached?: (copy: DetachedCopy) => void
  className?: string
}

/**
 * Header status for one resource: the managed badge plus a detach button
 * for workspace admins, or a "Detached from" note on a detached copy.
 * Renders nothing for ordinary resources.
 */
export function ManagedResourceStatus({
  resourceType,
  id,
  name,
  showDetach = false,
  compact = false,
  onDetached,
  className,
}: ManagedResourceStatusProps) {
  const { getManaged, getDetachedCopy } = useManagedResources()
  const managed = getManaged(resourceType, id)

  if (!managed) {
    const detached = getDetachedCopy(resourceType, id)
    if (!detached || compact) {
      return null
    }
    return <DetachedNote detached={detached} className={className} />
  }

  return (
    <span className={cn("inline-flex items-center gap-2", className)}>
      <ManagedBadge managed={managed} compact={compact} />
      {showDetach && (
        <DetachButton
          resourceType={resourceType}
          localId={managed.local_id}
          name={name}
          onDetached={onDetached}
        />
      )}
    </span>
  )
}

/** "Detach to editable copy" button and dialog, shown to workspace admins. */
function DetachButton({
  resourceType,
  localId,
  name,
  onDetached,
}: {
  resourceType: SyncResourceType
  localId: string
  name: string
  onDetached?: (copy: DetachedCopy) => void
}) {
  const canDetach = useScopeCheck("workspace:update") === true
  const [detachOpen, setDetachOpen] = useState(false)
  if (!canDetach) {
    return null
  }
  return (
    <>
      <Button
        type="button"
        variant="outline"
        size="sm"
        className="h-6 gap-1 px-2 text-xs"
        onClick={(event) => {
          // Rows that open on click should not also navigate.
          event.stopPropagation()
          setDetachOpen(true)
        }}
      >
        <UnlinkIcon className="size-3" />
        Detach to editable copy
      </Button>
      <DetachDialog
        open={detachOpen}
        onOpenChange={setDetachOpen}
        resourceType={resourceType}
        localId={localId}
        currentName={name}
        onDetached={onDetached}
      />
    </>
  )
}
