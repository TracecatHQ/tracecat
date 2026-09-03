"use client"

import { Loader2 } from "lucide-react"
import { useEffect, useState } from "react"
import type { SkillReadMinimal } from "@/client"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"

type DeleteSkillDialogProps = {
  open: boolean
  onOpenChange: (open: boolean) => void
  skill: SkillReadMinimal | null
  pending: boolean
  onConfirm: () => Promise<void> | void
}

/**
 * Confirmation dialog for deleting a skill.
 */
export function DeleteSkillDialog({
  open,
  onOpenChange,
  skill,
  pending,
  onConfirm,
}: DeleteSkillDialogProps) {
  const [confirmationText, setConfirmationText] = useState("")
  const skillName = skill?.name ?? ""
  const canDelete = skillName.length > 0 && confirmationText === skillName

  useEffect(() => {
    if (open) {
      setConfirmationText("")
    }
  }, [open, skillName])

  return (
    <AlertDialog
      open={open}
      onOpenChange={(isOpen) => {
        if (!pending) {
          onOpenChange(isOpen)
        }
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete skill</AlertDialogTitle>
          <AlertDialogDescription>
            Delete{" "}
            <span className="font-medium text-foreground">{skill?.name}</span>?
            This removes it from Skills Studio and prevents future agent
            bindings. Skills referenced by current or previous agent versions
            cannot be deleted.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <div className="flex flex-col gap-2">
          <Label htmlFor="delete-skill-confirmation">
            Type the skill name to confirm
          </Label>
          <Input
            id="delete-skill-confirmation"
            value={confirmationText}
            onChange={(event) => setConfirmationText(event.target.value)}
            placeholder={skillName}
            disabled={pending}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
          />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={pending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            disabled={pending || !canDelete}
            onClick={(event) => {
              event.preventDefault()
              if (!canDelete) {
                return
              }
              void onConfirm()
            }}
            variant="destructive"
          >
            {pending && <Loader2 className="mr-2 size-4 animate-spin" />}
            Delete
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
