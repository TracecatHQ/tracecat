"use client"

import { Loader2 } from "lucide-react"
import type { ReactNode } from "react"
import { useState } from "react"
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

interface ConfirmDestructiveDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /**
   * The exact string the user must type to enable confirmation. Typically
   * the name of the resource being destroyed.
   */
  confirmPhrase: string
  title: ReactNode
  description: ReactNode
  /**
   * Label for the confirmation button. Defaults to "Delete".
   */
  confirmLabel?: string
  /**
   * Optional placeholder for the confirmation input. Defaults to the
   * `confirmPhrase`.
   */
  inputPlaceholder?: string
  isPending?: boolean
  onConfirm: () => void | Promise<void>
}

/**
 * Destructive-action confirmation that requires the user to type a specific
 * phrase before the confirm button is enabled.
 *
 * Used for disconnecting OAuth integrations and removing workspace MCP
 * servers — the type-the-name gate prevents one-click destruction of
 * connections that may be in active use.
 */
export function ConfirmDestructiveDialog({
  open,
  onOpenChange,
  confirmPhrase,
  title,
  description,
  confirmLabel = "Delete",
  inputPlaceholder,
  isPending = false,
  onConfirm,
}: ConfirmDestructiveDialogProps) {
  const [confirmText, setConfirmText] = useState("")

  const matches = confirmText.trim() === confirmPhrase

  function handleOpenChange(next: boolean) {
    if (!next) setConfirmText("")
    onOpenChange(next)
  }

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <div className="space-y-2">
          <Label htmlFor="confirm-destructive-input">
            Type <strong>{confirmPhrase}</strong> to confirm:
          </Label>
          <Input
            id="confirm-destructive-input"
            value={confirmText}
            onChange={(event) => setConfirmText(event.target.value)}
            placeholder={inputPlaceholder ?? confirmPhrase}
            disabled={isPending}
            autoComplete="off"
          />
        </div>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={isPending || !matches}
            onClick={async (event) => {
              event.preventDefault()
              if (!matches) return
              try {
                await onConfirm()
              } catch {
                // Mutation callbacks handle user-facing errors.
              }
            }}
          >
            {isPending ? (
              <Loader2 className="mr-2 size-4 animate-spin" />
            ) : null}
            {confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
