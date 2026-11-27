import type * as React from "react"

import { toastManager } from "@/components/ui/toast"

/**
 * Toast severity. `destructive` is for real failures only; use `warning` for
 * user-correctable problems such as validation, conflicts or missing access.
 */
type ToastVariant =
  | "default"
  | "success"
  | "info"
  | "warning"
  | "destructive"
  | "loading"

/** Options accepted by `toast()`. */
interface ToastOptions {
  title?: React.ReactNode
  description?: React.ReactNode
  variant?: ToastVariant
  /** Props for the toast's action button, e.g. `{ children: "View PR", onClick }`. */
  action?: React.ComponentPropsWithoutRef<"button">
  /** Auto-dismiss delay in ms; `0` keeps the toast open. Defaults to 5000. */
  duration?: number
}

const VARIANT_TYPES: Record<ToastVariant, string | undefined> = {
  default: undefined,
  success: "success",
  info: "info",
  warning: "warning",
  destructive: "error",
  loading: "loading",
}

/** Show a toast through the shared Base UI toast manager. */
function toast({
  variant = "default",
  action,
  duration,
  ...options
}: ToastOptions) {
  const id = toastManager.add({
    ...options,
    type: VARIANT_TYPES[variant],
    actionProps: action,
    timeout: duration,
    priority: variant === "destructive" ? "high" : "low",
  })

  return {
    id,
    dismiss: () => toastManager.close(id),
    update: (next: ToastOptions) => {
      const {
        variant: nextVariant,
        action: nextAction,
        duration: nextDuration,
        ...rest
      } = next
      toastManager.update(id, {
        ...rest,
        ...(nextVariant ? { type: VARIANT_TYPES[nextVariant] } : {}),
        ...(nextAction ? { actionProps: nextAction } : {}),
        ...(nextDuration !== undefined ? { timeout: nextDuration } : {}),
      })
    },
  }
}

/** Hook form of `toast()` kept for existing callers. */
function useToast() {
  return {
    toast,
    dismiss: (toastId?: string) => toastManager.close(toastId),
  }
}

export { type ToastOptions, type ToastVariant, toast, useToast }
