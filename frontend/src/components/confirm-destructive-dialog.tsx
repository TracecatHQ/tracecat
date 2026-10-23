"use client"

import { Loader2 } from "lucide-react"
import type { ReactNode } from "react"
import { useId, useState } from "react"
import { CopyButton } from "@/components/copy-button"
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

export interface ConfirmDestructiveIdentifier {
  /** Human label for the identifier, e.g. "Workflow ID". */
  label: string
  value: string
}

interface ConfirmDestructiveDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  /**
   * The exact string the user must type to enable confirmation, e.g. the
   * resource ID, slug, or name.
   */
  confirmPhrase: string
  /**
   * Identifier shown in the dialog with a copy button so the user can paste
   * it into the confirmation input.
   */
  identifier?: ConfirmDestructiveIdentifier
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
  /**
   * Rendered inside the dialog root, e.g. an `AlertDialogTrigger`.
   */
  children?: ReactNode
}

/**
 * Destructive-action confirmation that requires the user to type a specific
 * phrase before the confirm button is enabled.
 *
 * Use this for every hard delete of a user-built resource (cases, workflows,
 * non-empty folders, agents, skills, tables, integrations) so the gate looks
 * and behaves the same everywhere.
 */
export function ConfirmDestructiveDialog({
  open,
  onOpenChange,
  confirmPhrase,
  identifier,
  title,
  description,
  confirmLabel = "Delete",
  inputPlaceholder,
  isPending = false,
  onConfirm,
  children,
}: ConfirmDestructiveDialogProps) {
  const inputId = useId()
  const [confirmText, setConfirmText] = useState("")

  const matches =
    confirmPhrase.length > 0 && confirmText.trim() === confirmPhrase

  function handleOpenChange(next: boolean) {
    if (isPending) return
    if (!next) setConfirmText("")
    onOpenChange(next)
  }

  async function handleConfirm() {
    if (!matches) return
    try {
      await onConfirm()
      setConfirmText("")
    } catch {
      // Mutation callbacks handle user-facing errors.
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      {children}
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{title}</AlertDialogTitle>
          <AlertDialogDescription asChild>
            <div className="space-y-2">{description}</div>
          </AlertDialogDescription>
        </AlertDialogHeader>
        {identifier ? (
          <div className="flex items-center justify-between gap-3 rounded-md border bg-muted/40 px-3 py-2">
            <div className="flex min-w-0 flex-col">
              <span className="text-xs text-muted-foreground">
                {identifier.label}
              </span>
              <code
                className="truncate font-mono text-sm"
                data-testid="confirm-destructive-identifier"
              >
                {identifier.value}
              </code>
            </div>
            <CopyButton
              value={identifier.value}
              toastMessage={`Copied ${identifier.label.toLowerCase()} to clipboard`}
              tooltipMessage={`Copy ${identifier.label.toLowerCase()}`}
              className="size-7 shrink-0"
              iconClassName="size-3.5"
            />
          </div>
        ) : null}
        <form
          className="space-y-2"
          onSubmit={(event) => {
            event.preventDefault()
            void handleConfirm()
          }}
        >
          <Label htmlFor={inputId} className="font-normal">
            Type{" "}
            <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs font-medium">
              {confirmPhrase}
            </code>{" "}
            to confirm
          </Label>
          <Input
            id={inputId}
            value={confirmText}
            onChange={(event) => setConfirmText(event.target.value)}
            placeholder={inputPlaceholder ?? confirmPhrase}
            disabled={isPending}
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
          />
        </form>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={isPending}>Cancel</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={isPending || !matches}
            onClick={(event) => {
              event.preventDefault()
              void handleConfirm()
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
