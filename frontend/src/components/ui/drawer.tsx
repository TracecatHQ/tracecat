"use client"

import { Drawer as DrawerPrimitive } from "@base-ui/react/drawer"
import * as React from "react"

import { cn } from "@/lib/utils"

type DrawerContextProps = {
  modal: DrawerPrimitive.Root.Props["modal"]
}

const DrawerContext = React.createContext<DrawerContextProps | null>(null)

function useDrawer() {
  const context = React.useContext(DrawerContext)

  if (!context) {
    throw new Error("useDrawer must be used within a Drawer.")
  }

  return context
}

/**
 * shadcn/ui base drawer (Base UI), ported to Tailwind v3. Pass
 * `modal={false}` for a side panel that leaves the page interactive.
 */
function Drawer({
  modal = true,
  swipeDirection = "down",
  ...props
}: DrawerPrimitive.Root.Props) {
  const contextValue = React.useMemo(() => ({ modal }), [modal])

  return (
    <DrawerContext.Provider value={contextValue}>
      <DrawerPrimitive.Root
        data-slot="drawer"
        modal={modal}
        swipeDirection={swipeDirection}
        {...props}
      />
    </DrawerContext.Provider>
  )
}

function DrawerTrigger({ ...props }: DrawerPrimitive.Trigger.Props) {
  return <DrawerPrimitive.Trigger data-slot="drawer-trigger" {...props} />
}

function DrawerPortal({ ...props }: DrawerPrimitive.Portal.Props) {
  return <DrawerPrimitive.Portal data-slot="drawer-portal" {...props} />
}

function DrawerClose({ ...props }: DrawerPrimitive.Close.Props) {
  return <DrawerPrimitive.Close data-slot="drawer-close" {...props} />
}

function DrawerOverlay({
  className,
  ...props
}: DrawerPrimitive.Backdrop.Props) {
  return (
    <DrawerPrimitive.Backdrop
      data-slot="drawer-overlay"
      className={cn(
        "fixed inset-0 z-50 min-h-dvh select-none bg-black/10 opacity-[calc(1-var(--drawer-swipe-progress,0))] transition-opacity duration-[450ms] ease-[cubic-bezier(0.32,0.72,0,1)] data-[ending-style]:pointer-events-none data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 data-[swiping]:duration-0",
        className
      )}
      {...props}
    />
  )
}

function DrawerContent({
  className,
  children,
  ...props
}: DrawerPrimitive.Popup.Props) {
  const { modal } = useDrawer()

  return (
    <DrawerPortal data-slot="drawer-portal">
      {modal === true && <DrawerOverlay />}
      <DrawerPrimitive.Viewport
        data-slot="drawer-viewport"
        data-modal={modal}
        className="pointer-events-none fixed inset-0 z-50 select-none data-[modal=true]:pointer-events-auto"
      >
        <DrawerPrimitive.Popup
          data-slot="drawer-popup"
          className={cn(
            // Base.
            "group/drawer-popup pointer-events-auto fixed z-50 flex min-h-0 select-none flex-col bg-popover text-sm text-popover-foreground outline-none [transform:translate3d(var(--translate-x,0px),var(--translate-y,0px),0)] transition-[transform,opacity] duration-[450ms] ease-[cubic-bezier(0.22,1,0.36,1)] will-change-transform",
            // Transitions.
            "data-[ending-style]:[transform:var(--closed-transform)] data-[starting-style]:[transform:var(--closed-transform)] data-[ending-style]:duration-[calc(var(--drawer-swipe-strength,1)*400ms)] data-[swiping]:duration-0",
            // Direction: down.
            "data-[swipe-direction=down]:inset-x-0 data-[swipe-direction=down]:bottom-0 data-[swipe-direction=down]:max-h-[calc(100dvh-6rem)] data-[swipe-direction=down]:rounded-t-xl data-[swipe-direction=down]:border-t data-[swipe-direction=down]:[--closed-transform:translate3d(0,calc(100%+2px),0)] data-[swipe-direction=down]:[--translate-y:var(--drawer-swipe-movement-y,0px)]",
            // Direction: up.
            "data-[swipe-direction=up]:inset-x-0 data-[swipe-direction=up]:top-0 data-[swipe-direction=up]:max-h-[calc(100dvh-6rem)] data-[swipe-direction=up]:rounded-b-xl data-[swipe-direction=up]:border-b data-[swipe-direction=up]:[--closed-transform:translate3d(0,calc(-100%-2px),0)] data-[swipe-direction=up]:[--translate-y:var(--drawer-swipe-movement-y,0px)]",
            // Direction: left.
            "data-[swipe-direction=left]:inset-y-0 data-[swipe-direction=left]:left-0 data-[swipe-direction=left]:w-3/4 data-[swipe-direction=left]:border-r data-[swipe-direction=left]:sm:max-w-sm data-[swipe-direction=left]:[--closed-transform:translate3d(calc(-100%-2px),0,0)] data-[swipe-direction=left]:[--translate-x:var(--drawer-swipe-movement-x,0px)]",
            // Direction: right.
            "data-[swipe-direction=right]:inset-y-0 data-[swipe-direction=right]:right-0 data-[swipe-direction=right]:w-3/4 data-[swipe-direction=right]:border-l data-[swipe-direction=right]:sm:max-w-sm data-[swipe-direction=right]:[--closed-transform:translate3d(calc(100%+2px),0,0)] data-[swipe-direction=right]:[--translate-x:var(--drawer-swipe-movement-x,0px)]",
            className
          )}
          {...props}
        >
          <DrawerPrimitive.Content
            data-slot="drawer-content"
            className="flex min-h-0 flex-1 select-text flex-col overflow-hidden overscroll-contain rounded-[inherit] group-data-[swiping]/drawer-popup:select-none"
          >
            {children}
          </DrawerPrimitive.Content>
        </DrawerPrimitive.Popup>
      </DrawerPrimitive.Viewport>
    </DrawerPortal>
  )
}

function DrawerHeader({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="drawer-header"
      className={cn("flex shrink-0 flex-col gap-0.5 p-4 pb-0", className)}
      {...props}
    />
  )
}

function DrawerFooter({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      data-slot="drawer-footer"
      className={cn("mt-auto flex shrink-0 flex-col gap-2 p-4 pt-0", className)}
      {...props}
    />
  )
}

function DrawerTitle({ className, ...props }: DrawerPrimitive.Title.Props) {
  return (
    <DrawerPrimitive.Title
      data-slot="drawer-title"
      className={cn("text-base font-medium text-foreground", className)}
      {...props}
    />
  )
}

function DrawerDescription({
  className,
  ...props
}: DrawerPrimitive.Description.Props) {
  return (
    <DrawerPrimitive.Description
      data-slot="drawer-description"
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  )
}

export {
  Drawer,
  DrawerPortal,
  DrawerOverlay,
  DrawerTrigger,
  DrawerClose,
  DrawerContent,
  DrawerHeader,
  DrawerFooter,
  DrawerTitle,
  DrawerDescription,
}
