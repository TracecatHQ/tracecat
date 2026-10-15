import { cn } from "@/lib/utils"

/**
 * Class constants for rendered forms and the requester pages.
 *
 * People who fill in forms are not product users, so these surfaces use a
 * larger type scale than the builder: 14 px body and inputs and 36 px
 * controls. The scale lives here, applied through `className`, so the shared
 * UI primitives stay untouched and a visual change is a one-file edit.
 */
export const formStyles = {
  /** The bordered card a form or its confirmation sits in. */
  card: "flex w-full flex-col gap-6 rounded-lg border bg-background p-5 text-sm sm:p-8",
  title: "text-xl font-semibold leading-7",
  description: "mt-1 text-sm leading-5 text-muted-foreground",
  heading: "border-t pt-5 text-base font-semibold leading-6",
  field: "flex flex-col gap-1.5",
  label: "text-sm font-medium leading-5",
  required: "text-destructive",
  help: "text-xs leading-4 text-muted-foreground",
  error: "text-xs leading-4 text-destructive",
  /** 36 px tall, 14 px text. Merged over a primitive's own size classes. */
  control: "h-9 text-sm shadow-none",
  controlInvalid: "border-destructive",
  /** Popover and menu rows. */
  option: "text-sm",
  footer:
    "flex flex-wrap items-center justify-between gap-3 border-t pt-5 text-xs text-muted-foreground",
  button: "h-9 px-4 text-sm",
} as const

/** Classes for a single-line control, with the invalid border when needed. */
export function formControlClass(invalid: boolean, className?: string): string {
  return cn(formStyles.control, invalid && formStyles.controlInvalid, className)
}

/** Class constants for the pages around a form: top bar, catalog, states. */
export const requesterStyles = {
  /** `body` is `overflow-hidden`, so the frame owns the viewport height. */
  frame: "flex h-dvh flex-col bg-background",
  topBar:
    "flex h-12 flex-none items-center gap-2.5 border-b bg-background px-4",
  topBarTitle: "truncate text-sm font-medium",
  main: "min-h-0 flex-1 overflow-y-auto bg-muted/30",
  /** Column for a single form. */
  formColumn: "mx-auto w-full max-w-xl px-4 py-4 sm:py-10",
  /** Column for the catalog and the submissions table. */
  catalogColumn: "mx-auto w-full max-w-3xl px-4 py-10",
  pageTitle: "text-2xl font-semibold leading-8",
  list: "overflow-hidden rounded-lg border bg-background",
} as const
