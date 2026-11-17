"use client"

import { ConfirmDestructiveDialog } from "@/components/confirm-destructive-dialog"

/**
 * Confirmation dialog for deleting an agent. The user types the agent slug.
 */
export function AgentPresetDeleteDialog({
  open,
  onOpenChange,
  presetName,
  presetSlug,
  isDeleting,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (nextOpen: boolean) => void
  presetName: string
  presetSlug: string
  isDeleting: boolean
  onConfirm: () => Promise<void> | void
}) {
  return (
    <ConfirmDestructiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Delete agent"
      description={<AgentDeleteDescription name={presetName} />}
      identifier={{ label: "Agent slug", value: presetSlug }}
      confirmPhrase={presetSlug}
      confirmLabel="Delete"
      isPending={isDeleting}
      onConfirm={onConfirm}
    />
  )
}

export function AgentDeleteDescription({ name }: { name: string }) {
  return (
    <>
      Permanently delete{" "}
      <span className="font-medium text-foreground">{name}</span>? It will also
      be removed as a subagent from every agent that uses it, including saved
      versions. Restoring an older agent version will not bring this subagent
      back. This cannot be undone.
    </>
  )
}
