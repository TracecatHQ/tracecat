"use client"

import * as DialogPrimitive from "@radix-ui/react-dialog"
import { Cross2Icon } from "@radix-ui/react-icons"
import * as React from "react"

import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"

const Dialog = DialogPrimitive.Root

/**
 * Fixed heights for panel dialogs, capped to the viewport so they never
 * overflow small screens.
 */
const DIALOG_PANEL_HEIGHTS = {
  sm: "h-[min(calc(100dvh-2rem),320px)]",
  md: "h-[min(calc(100dvh-2rem),520px)]",
  lg: "h-[min(calc(100dvh-2rem),680px)]",
  xl: "h-[min(calc(100dvh-2rem),840px)]",
} as const

/** Fixed height preset for a panel dialog. */
export type DialogPanelSize = keyof typeof DIALOG_PANEL_HEIGHTS

const DialogPanelContext = React.createContext(false)

/**
 * Props that make a Dialog dismissable only through its top-right close button.
 * Blocks backdrop clicks, outside focus, and Escape.
 */
export const nonDismissableDialogProps = {
  onPointerDownOutside: (event: Event) => event.preventDefault(),
  onInteractOutside: (event: Event) => event.preventDefault(),
  onEscapeKeyDown: (event: KeyboardEvent) => event.preventDefault(),
} as const

const DialogTrigger = DialogPrimitive.Trigger

const DialogPortal = DialogPrimitive.Portal

const DialogClose = DialogPrimitive.Close

const DialogOverlay = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Overlay>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Overlay>
>(({ className, ...props }, ref) => (
  <DialogPrimitive.Overlay
    ref={ref}
    className={cn(
      "fixed inset-0 z-50 bg-black/50 backdrop-blur-sm data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0",
      className
    )}
    {...props}
  />
))
DialogOverlay.displayName = DialogPrimitive.Overlay.displayName

const DialogContent = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Content>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Content> & {
    /**
     * Fallback label used when no visible DialogTitle is provided.
     * Screen-reader only.
     */
    title?: string
    /**
     * Render as a flat panel: bordered header and footer strips around a
     * scrollable `DialogBody`, at a fixed height. Matches the tool picker.
     */
    size?: DialogPanelSize
  }
>(({ className, children, title = "Dialog", size, ...props }, ref) => {
  // Determine if the consumer already supplied a DialogTitle
  const hasTitle = React.Children.toArray(children).some(
    (child) => React.isValidElement(child) && child.type === DialogTitle
  )

  return (
    <DialogPortal>
      <DialogOverlay />
      <DialogPanelContext.Provider value={size !== undefined}>
        <DialogPrimitive.Content
          ref={ref}
          className={cn(
            "fixed left-1/2 top-1/2 z-50 grid max-h-[calc(100dvh-2rem)] w-full max-w-lg -translate-x-1/2 -translate-y-1/2 gap-4 overflow-y-auto border bg-background p-6 shadow-lg duration-200 data-[state=open]:animate-in data-[state=closed]:animate-out data-[state=closed]:fade-out-0 data-[state=open]:fade-in-0 data-[state=closed]:zoom-out-95 data-[state=open]:zoom-in-95 data-[state=closed]:slide-out-to-left-1/2 data-[state=closed]:slide-out-to-top-[48%] data-[state=open]:slide-in-from-left-1/2 data-[state=open]:slide-in-from-top-[48%] sm:rounded-lg",
            size &&
              cn(
                "flex flex-col gap-0 overflow-hidden p-0 shadow-none",
                DIALOG_PANEL_HEIGHTS[size]
              ),
            className
          )}
          {...props}
        >
          {/* Inject a hidden title when none exists to satisfy accessibility */}
          {!hasTitle && <DialogTitle className="sr-only">{title}</DialogTitle>}

          {children}

          <DialogPrimitive.Close className="absolute right-4 top-4 rounded-sm opacity-70 transition-opacity hover:opacity-100 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-ring disabled:pointer-events-none data-[state=open]:bg-accent data-[state=open]:text-muted-foreground">
            <Cross2Icon className="size-4" />
            <span className="sr-only">Close</span>
          </DialogPrimitive.Close>
        </DialogPrimitive.Content>
      </DialogPanelContext.Provider>
    </DialogPortal>
  )
})
DialogContent.displayName = DialogPrimitive.Content.displayName

const DialogHeader = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => {
  const panel = React.useContext(DialogPanelContext)
  return (
    <div
      className={cn(
        "flex flex-col space-y-1.5 text-center sm:text-left",
        panel && "shrink-0 space-y-1 border-b px-4 py-3 pr-12 text-left",
        className
      )}
      {...props}
    />
  )
}
DialogHeader.displayName = "DialogHeader"

/**
 * Scrollable middle section of a panel dialog. Fills the space between the
 * header and footer; children may use `flex-1` to fill the visible height.
 */
const DialogBody = React.forwardRef<
  HTMLDivElement,
  React.HTMLAttributes<HTMLDivElement>
>(({ className, ...props }, ref) => (
  <ScrollArea className="min-h-0 flex-1 [&>[data-radix-scroll-area-viewport]>div]:!flex [&>[data-radix-scroll-area-viewport]>div]:min-h-full [&>[data-radix-scroll-area-viewport]>div]:flex-col">
    <div
      ref={ref}
      className={cn("flex flex-1 flex-col gap-4 p-4 text-sm", className)}
      {...props}
    />
  </ScrollArea>
))
DialogBody.displayName = "DialogBody"

const DialogFooter = ({
  className,
  ...props
}: React.HTMLAttributes<HTMLDivElement>) => {
  const panel = React.useContext(DialogPanelContext)
  return (
    <div
      className={cn(
        "flex flex-col-reverse sm:flex-row sm:justify-end sm:space-x-2",
        panel &&
          "mt-auto h-14 shrink-0 flex-row items-center justify-end gap-2 border-t px-4 sm:space-x-0",
        className
      )}
      {...props}
    />
  )
}
DialogFooter.displayName = "DialogFooter"

const DialogTitle = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Title>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Title>
>(({ className, ...props }, ref) => {
  const panel = React.useContext(DialogPanelContext)
  return (
    <DialogPrimitive.Title
      ref={ref}
      className={cn(
        "text-lg font-semibold leading-none tracking-tight",
        panel && "text-sm leading-5",
        className
      )}
      {...props}
    />
  )
})
DialogTitle.displayName = DialogPrimitive.Title.displayName

const DialogDescription = React.forwardRef<
  React.ElementRef<typeof DialogPrimitive.Description>,
  React.ComponentPropsWithoutRef<typeof DialogPrimitive.Description>
>(({ className, ...props }, ref) => {
  const panel = React.useContext(DialogPanelContext)
  return (
    <DialogPrimitive.Description
      ref={ref}
      className={cn(
        "text-sm text-muted-foreground",
        panel && "text-xs",
        className
      )}
      {...props}
    />
  )
})
DialogDescription.displayName = DialogPrimitive.Description.displayName

export {
  Dialog,
  DialogPortal,
  DialogOverlay,
  DialogTrigger,
  DialogClose,
  DialogContent,
  DialogBody,
  DialogHeader,
  DialogFooter,
  DialogTitle,
  DialogDescription,
}
