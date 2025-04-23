"use client"

import * as React from "react"

import { DropdownMenuItem } from "@/components/ui/dropdown-menu"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"

interface ToggleableDropdownItemProps
  extends React.ComponentPropsWithoutRef<typeof DropdownMenuItem> {
  disabledMessage?: string
  enabled?: boolean
  onSelect?: () => void
}
export function ToggleableDropdownItem({
  children,
  disabledMessage = "This option is currently disabled",
  enabled = true,
  onSelect,
  ...props
}: ToggleableDropdownItemProps) {
  // Handle item selection only when enabled
  const handleItemClick = (e: React.MouseEvent<HTMLDivElement>) => {
    e.stopPropagation()
    if (enabled && onSelect) {
      onSelect()
    }
  }

  if (enabled) {
    return (
      <DropdownMenuItem {...props} onClick={handleItemClick}>
        {children}
      </DropdownMenuItem>
    )
  }

  return (
    <TooltipProvider>
      <Tooltip>
        <TooltipTrigger asChild>
          <div
            className="cursor-not-allowed"
            onClick={(e) => e.stopPropagation()}
          >
            <DropdownMenuItem
              {...props}
              disabled
              onClick={(e) => e.stopPropagation()}
            >
              {children}
            </DropdownMenuItem>
          </div>
        </TooltipTrigger>
        <TooltipContent>
          <p>{disabledMessage}</p>
        </TooltipContent>
      </Tooltip>
    </TooltipProvider>
  )
}
