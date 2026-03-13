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

  useEffect(() => {
    if (!open) {
      setConfirmationValue("")
    }
  }, [open])

  useEffect(() => {
    setConfirmationValue("")
  }, [presetName])

  const isConfirmationValid = confirmationValue === presetName

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
            {presetName ? `"${presetName}"` : "the agent"} and cannot be undone.
          </AlertDialogDescription>
          <AlertDialogDescription>
            Type <b>{presetName}</b> to confirm deletion.
          </AlertDialogDescription>
        </AlertDialogHeader>
        <Input
          value={confirmationValue}
          onChange={(event) => setConfirmationValue(event.target.value)}
          placeholder={`Type "${presetName}" to confirm`}
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
