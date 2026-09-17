"use client"

import { GripVertical } from "lucide-react"
import { useDragDivider } from "@/lib/hooks"
import { cn } from "@/lib/utils"

export const DEFAULT_MIN = 400
export const DEFAULT_MAX = 600

export interface DragDividerProps {
  /** Current size (width for vertical divider, height for horizontal) in pixels */
  value: number
  /** Callback fired with the new size while dragging */
  onChange: (newSize: number) => void
  /** Orientation of the divider. Defaults to "vertical" (i.e. a vertical bar that resizes width) */
  orientation?: "vertical" | "horizontal"
  /** Minimum size constraint in pixels */
  min?: number
  /** Maximum size constraint in pixels */
  max?: number
  /** Additional Tailwind classes */
  className?: string
}

/**
 * DragDivider provides a simple draggable separator for resizing two adjacent panels.
 * It is intentionally lightweight and framework-agnostic so it can be dropped into
 * any layout that manages its own size state.
 */
export function DragDivider({
  value,
  onChange,
  orientation = "vertical",
  min = DEFAULT_MIN,
  max = DEFAULT_MAX,
  className,
}: DragDividerProps) {
  const { isDragging, dragHandleProps } = useDragDivider({
    value,
    onChange,
    orientation,
    min,
    max,
  })

  return (
    <div
      data-orientation={orientation}
      {...dragHandleProps}
      className={cn(
        // Base styles: a 1px border line with a wider invisible grab area
        "group relative z-10 flex shrink-0 items-center justify-center bg-border",
        "before:absolute before:z-10 before:content-['']",
        orientation === "vertical"
          ? "w-px before:inset-y-0 before:left-1/2 before:w-4 before:-translate-x-1/2"
          : "h-px before:inset-x-0 before:top-1/2 before:h-4 before:-translate-y-1/2",
        // Visual feedback while hovering or dragging
        orientation === "vertical"
          ? "after:absolute after:inset-y-0 after:left-1/2 after:w-0.5 after:-translate-x-1/2"
          : "after:absolute after:inset-x-0 after:top-1/2 after:h-0.5 after:-translate-y-1/2",
        "after:bg-transparent after:transition-colors after:duration-150 group-hover:after:bg-ring/40",
        isDragging && "after:bg-ring/60",
        className
      )}
    >
      {/* Grip icon shows on hover */}
      <GripVertical
        className={cn(
          "z-20 h-4 w-4 rounded-sm bg-background text-muted-foreground opacity-0 transition-opacity duration-150 group-hover:opacity-100",
          orientation === "horizontal" && "rotate-90",
          isDragging && "opacity-100"
        )}
      />
    </div>
  )
}
