"use client"

import type React from "react"
import { useState } from "react"
import {
  DEFAULT_MAX,
  DEFAULT_MIN,
  DragDivider,
} from "@/components/drag-divider"
import { SidebarInset } from "@/components/ui/sidebar"
import { cn } from "@/lib/utils"

interface ResizableSidebarProps {
  children: React.ReactNode
  /** Which side of the flex row the sidebar should appear on. Default: right */
  side?: "left" | "right"
  /** Initial width in px */
  initial?: number
  /** Min width in px */
  min?: number
  /** Max width in px */
  max?: number
  /** Additional classes for the SidebarInset wrapper */
  insetClassName?: string
  /** Additional classes for the DragDivider */
  dividerClassName?: string
}

/**
 * ResizableSidebar composes a SidebarInset with a DragDivider to provide a
 * draggable panel that can be placed on the left or right of a flex row.
 * All sizing state and clamping logic is handled internally so layouts can
 * simply drop this component in.
 */
export function ResizableSidebar({
  children,
  side = "right",
  initial = DEFAULT_MIN,
  min = DEFAULT_MIN,
  max = DEFAULT_MAX,
  insetClassName,
  dividerClassName,
}: ResizableSidebarProps) {
  const [width, setWidth] = useState<number>(initial)

  const insetClasses = cn(
    "flex-none md:peer-data-[variant=inset]:!ml-0 md:peer-data-[state=collapsed]:peer-data-[variant=inset]:!ml-0",
    insetClassName,
    {
      "ml-px": side === "right",
      "mr-px": side === "left",
    }
  )

  const divider = (
    <DragDivider
      className={cn("w-1.5 shrink-0", dividerClassName)}
      value={width}
      onChange={setWidth}
      min={min}
      max={max}
    />
  )

  return (
    <>
      {side === "right" ? divider : null}
      <SidebarInset
        className={insetClasses}
        // Force the inset to honor the drag width even when surrounding flex utilities re-run.
        style={{ width, minWidth: min, maxWidth: max }}
      >
        {children}
      </SidebarInset>
      {side === "left" ? divider : null}
    </>
  )
}
