"use client"

import { useEffect, useState } from "react"
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

export function AgentPresetDeleteDialog({
  open,
  onOpenChange,
  presetName,
  isDeleting,
  onConfirm,
}: {
  open: boolean
  onOpenChange: (nextOpen: boolean) => void
  presetName: string
  isDeleting: boolean
  onConfirm: () => Promise<void> | void
}) {
  const [confirmationValue, setConfirmationValue] = useState("")
  const normalizedPresetName = presetName.trim()
  const confirmationTarget = normalizedPresetName || "DELETE"

  useEffect(() => {
    if (!open) {
      setConfirmationValue("")
    }
  }, [open])

  useEffect(() => {
    setConfirmationValue("")
  }, [presetName])

  const isConfirmationValid = confirmationValue === confirmationTarget

  return (
    <AlertDialog
      open={open}
      onOpenChange={(nextOpen) => {
        if (isDeleting) {
          return
        }
        if (!nextOpen) {
          setConfirmationValue("")
        }
        onOpenChange(nextOpen)
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Delete this agent?</AlertDialogTitle>
          <AlertDialogDescription>
            This action permanently removes{" "}
            {normalizedPresetName ? `"${normalizedPresetName}"` : "the agent"}{" "}
            and cannot be undone.
          </AlertDialogDescription>
          <AlertDialogDescription>
            Type <b>{confirmationTarget}</b> to confirm deletion.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <Input
          value={confirmationValue}
          onChange={(event) => setConfirmationValue(event.target.value)}
          placeholder={`Type "${confirmationTarget}" to confirm`}
          disabled={isDeleting}
        />
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isDeleting}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            onClick={(event) => {
              event.preventDefault()
              void onConfirm()
            }}
            disabled={isDeleting || !isConfirmationValid}
          >
            {isDeleting ? "Deleting..." : "Delete agent"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
