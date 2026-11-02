"use client"

import type { SkillReadMinimal } from "@/client"
import { ConfirmDestructiveDialog } from "@/components/confirm-destructive-dialog"

type DeleteSkillDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  skill: SkillReadMinimal | null
  pending: boolean
  onConfirm: () => Promise<void> | void
}

/**
 * Confirmation dialog for deleting a skill. The user types the skill slug.
 */
export function DeleteSkillDialog({
  open,
  onOpenChange,
  skill,
  pending,
  onConfirm,
}: DeleteSkillDialogProps) {
  const slug = skill?.slug ?? ""
  return (
    <ConfirmDestructiveDialog
      open={open}
      onOpenChange={onOpenChange}
      title="Delete skill"
      description={
        <>
          Delete{" "}
          <span className="font-medium text-foreground">{skill?.name}</span>?
          This removes it from Skills Studio and every agent that uses it,
          including saved versions. Restoring an older agent version will not
          bring this skill back. This cannot be undone.
        </>
      }
      identifier={{ label: "Skill slug", value: slug }}
      confirmPhrase={slug}
      isPending={pending}
      onConfirm={onConfirm}
    />
  )
}
