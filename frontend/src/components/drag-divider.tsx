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
        // Base styles
        "group relative flex shrink-0 items-center justify-center",
        // Add visual feedback when dragging
        isDragging && "opacity-80",
        className
      )}
    >
      {/* Slim line that appears on hover */}
      <div
        className={cn(
          "absolute bg-transparent transition-colors duration-150 group-hover:bg-border",
          orientation === "vertical" ? "inset-y-3 w-px" : "inset-x-3 h-px"
        )}
      />
      {/* Grip icon shows on hover */}
      <GripVertical
        className={cn(
          "text-border opacity-0 transition-opacity duration-150 group-hover:opacity-100",
          orientation === "horizontal" && "rotate-90",
          "h-4 w-4"
        )}
      />
    </div>
  )
}
