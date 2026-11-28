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

/** Action button props, plus an optional link target. */
type ToastActionOptions = React.ComponentPropsWithoutRef<"button"> & {
  href?: string
}

/** Options accepted by `toast()`. */
interface ToastOptions {
  title?: React.ReactNode
  description?: React.ReactNode
  variant?: ToastVariant
  /**
   * Toast action, e.g. `{ children: "View PR", onClick }`. Pass `href` to
   * render it as a link that opens in a new tab.
   */
  action?: ToastActionOptions
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

/**
 * Errors are announced assertively, unless the description is rich content:
 * Base UI repeats high-priority content in a hidden alert, which would
 * duplicate any links in it.
 */
function getPriority(
  variant: ToastVariant,
  description: React.ReactNode
): "high" | "low" {
  if (variant !== "destructive") {
    return "low"
  }
  return typeof description === "object" && description !== null
    ? "low"
    : "high"
}

function toActionFields(action: ToastActionOptions | undefined) {
  if (!action) {
    return {}
  }
  const { href, ...actionProps } = action
  return { actionProps, data: { actionHref: href } }
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
    ...toActionFields(action),
    timeout: duration,
    priority: getPriority(variant, options.description),
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
        ...(nextAction ? toActionFields(nextAction) : {}),
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

export {
  type ToastActionOptions,
  type ToastOptions,
  type ToastVariant,
  toast,
  useToast,
}
