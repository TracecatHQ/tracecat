"use client"

import { Loader2 } from "lucide-react"
import type { ReactNode } from "react"
import { useEffect, useId, useRef, useState } from "react"
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

/** Second "are you sure" step shown after the typed confirmation matches. */
export interface ConfirmDestructiveFinalStep {
  title: ReactNode
  description: ReactNode
  /** Label for the final confirm button. Defaults to `confirmLabel`. */
  confirmLabel?: string
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
  /**
   * Instruction above the input. Defaults to "Type the <identifier label> to
   * confirm".
   */
  confirmPrompt?: ReactNode
  title: ReactNode
  description: ReactNode
  /**
   * Label for the confirmation button. Defaults to "Delete".
   */
  confirmLabel?: string
  /**
   * When set, a matching phrase opens this extra confirmation step instead of
   * confirming immediately.
   */
  finalStep?: ConfirmDestructiveFinalStep
  isPending?: boolean
  onConfirm: () => void | Promise<void>
  /**
   * Rendered inside the dialog root, e.g. an `AlertDialogTrigger`.
   */
  children?: ReactNode
}

function lowerFirst(value: string): string {
  return value.charAt(0).toLowerCase() + value.slice(1)
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
  confirmPrompt,
  title,
  description,
  confirmLabel = "Delete",
  finalStep,
  isPending = false,
  onConfirm,
  children,
}: ConfirmDestructiveDialogProps) {
  const inputId = useId()
  const [confirmText, setConfirmText] = useState("")
  const [showFinalStep, setShowFinalStep] = useState(false)

  const matches =
    confirmPhrase.length > 0 && confirmText.trim() === confirmPhrase
  const onFinalStep = finalStep !== undefined && showFinalStep
  const cancelRef = useRef<HTMLButtonElement>(null)

  // The typed-input form unmounts on the final step, so move focus to Cancel
  // instead of letting it fall back to the dialog container.
  useEffect(() => {
    if (onFinalStep) cancelRef.current?.focus()
  }, [onFinalStep])

  function reset() {
    setConfirmText("")
    setShowFinalStep(false)
  }

  function handleOpenChange(next: boolean) {
    if (isPending) return
    if (!next) reset()
    onOpenChange(next)
  }

  async function handleConfirm() {
    if (!matches) return
    if (finalStep && !showFinalStep) {
      setShowFinalStep(true)
      return
    }
    try {
      await onConfirm()
      reset()
    } catch {
      // Mutation callbacks handle user-facing errors.
    }
  }

  let prompt: ReactNode = confirmPrompt
  if (prompt === undefined) {
    prompt = identifier ? (
      `Type the ${lowerFirst(identifier.label)} to confirm`
    ) : (
      <>
        Type{" "}
        <code className="rounded bg-muted px-1 py-0.5 font-mono text-xs font-medium">
          {confirmPhrase}
        </code>{" "}
        to confirm
      </>
    )
  }

  return (
    <AlertDialog open={open} onOpenChange={handleOpenChange}>
      {children}
      <AlertDialogContent className="focus:outline-none">
        {onFinalStep ? (
          <AlertDialogHeader>
            <AlertDialogTitle>{finalStep.title}</AlertDialogTitle>
            <AlertDialogDescription asChild>
              <div className="space-y-2">{finalStep.description}</div>
            </AlertDialogDescription>
          </AlertDialogHeader>
        ) : (
          <>
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
                  toastMessage={`Copied ${lowerFirst(identifier.label)} to clipboard`}
                  tooltipMessage={`Copy ${lowerFirst(identifier.label)}`}
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
                {prompt}
              </Label>
              <Input
                id={inputId}
                value={confirmText}
                onChange={(event) => setConfirmText(event.target.value)}
                disabled={isPending}
                autoComplete="off"
                autoCapitalize="none"
                spellCheck={false}
              />
            </form>
          </>
        )}
        <AlertDialogFooter>
          <AlertDialogCancel ref={cancelRef} disabled={isPending}>
            Cancel
          </AlertDialogCancel>
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
            {onFinalStep
              ? (finalStep.confirmLabel ?? confirmLabel)
              : confirmLabel}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
