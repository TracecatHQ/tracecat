"use client"

import { GripVertical } from "lucide-react"
import type React from "react"
import { cn } from "@/lib/utils"

export interface DragDividerProps {
  /** Current size (width for vertical divider, height for horizontal) in pixels */
  value: number
  /** Callback fired with the new size while dragging */
  onChange: (newSize: number) => void
  /** Minimum size allowed */
  min?: number
  /** Maximum size allowed */
  max?: number
  /** Orientation of the divider. Defaults to "vertical" (i.e. a vertical bar that resizes width) */
  orientation?: "vertical" | "horizontal"
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
  min = 100,
  max = 1000,
  orientation = "vertical",
  className,
}: DragDividerProps) {
  const handleMouseDown = (e: React.MouseEvent<HTMLDivElement, MouseEvent>) => {
    e.preventDefault()

    const startCoord = orientation === "vertical" ? e.clientX : e.clientY
    const startSize = value

    const onMouseMove = (moveEvent: MouseEvent) => {
      const currentCoord =
        orientation === "vertical" ? moveEvent.clientX : moveEvent.clientY
      const delta = startCoord - currentCoord
      let newSize = startSize + delta
      newSize = Math.min(Math.max(newSize, min), max)
      onChange(newSize)
    }

    const onMouseUp = () => {
      window.removeEventListener("mousemove", onMouseMove)
      window.removeEventListener("mouseup", onMouseUp)
    }

    window.addEventListener("mousemove", onMouseMove)
    window.addEventListener("mouseup", onMouseUp)
  }

  return (
    <div
      data-orientation={orientation}
      onMouseDown={handleMouseDown}
      className={cn(
        // Base styles
        "group relative flex shrink-0 items-center justify-center",
        // Cursor styles
        orientation === "vertical" ? "cursor-col-resize" : "cursor-row-resize",
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
