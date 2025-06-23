"use client"

import * as SwitchPrimitives from "@radix-ui/react-switch"
import * as React from "react"

import { cn } from "@/lib/utils"

type SwitchProps = React.ComponentPropsWithoutRef<
  typeof SwitchPrimitives.Root
> & {
  /**
   * The size variant of the switch
   * @default "sm"
   */
  size?: "xs" | "sm" | "md" | "lg"
}

const Switch = React.forwardRef<
  React.ElementRef<typeof SwitchPrimitives.Root>,
  SwitchProps
>(({ className, size = "sm", ...props }, ref) => (
  <SwitchPrimitives.Root
    className={cn(
      "peer inline-flex shrink-0 cursor-pointer items-center rounded-full border-2 border-transparent shadow-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-background disabled:cursor-not-allowed disabled:opacity-50 data-[state=checked]:bg-primary data-[state=unchecked]:bg-input",
      // Size variants for the root element
      size === "xs" && "h-3 w-6",
      size === "sm" && "h-4 w-7",
      size === "md" && "h-5 w-9",
      size === "lg" && "h-6 w-11",
      className
    )}
    {...props}
    ref={ref}
  >
    <SwitchPrimitives.Thumb
      className={cn(
        "pointer-events-none block rounded-full bg-background shadow-lg ring-0 transition-transform",
        // Size variants for the thumb element
        size === "xs" &&
          "size-2 data-[state=checked]:translate-x-3 data-[state=unchecked]:translate-x-0",
        size === "sm" &&
          "size-3 data-[state=checked]:translate-x-3 data-[state=unchecked]:translate-x-0",
        size === "md" &&
          "size-4 data-[state=checked]:translate-x-4 data-[state=unchecked]:translate-x-0",
        size === "lg" &&
          "size-5 data-[state=checked]:translate-x-5 data-[state=unchecked]:translate-x-0"
      )}
    />
  </SwitchPrimitives.Root>
))
Switch.displayName = SwitchPrimitives.Root.displayName

export { Switch }
