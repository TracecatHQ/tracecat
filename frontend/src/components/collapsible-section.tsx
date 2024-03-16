import React from "react"
import { CaretSortIcon } from "@radix-ui/react-icons"
import { LucideIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import DecoratedHeader, {
  DecoratedHeaderProps,
} from "@/components/decorated-header"

interface CollapsibleSectionProps extends DecoratedHeaderProps {
  icon?: LucideIcon
  title: string
  showToggleText?: boolean
  defaultIsOpen?: boolean
  children: React.ReactNode
}

function CollapsibleSection({
  icon: Icon,
  title,
  children,
  showToggleText = true,
  className,
  size = "xl",
  iconSize = "xl",
  iconProps,
  strokeWidth = 2.5,
  defaultIsOpen = false,
}: CollapsibleSectionProps) {
  const [isOpen, setIsOpen] = React.useState(defaultIsOpen)

  return (
    <Collapsible
      open={isOpen}
      onOpenChange={setIsOpen}
      className="w-full space-y-2"
    >
      <div className="flex items-center space-x-4">
        <CollapsibleTrigger asChild>
          <Button
            variant="ghost"
            size="sm"
            className="flex w-full items-center px-0 hover:bg-transparent"
          >
            <DecoratedHeader
              icon={Icon}
              title={title}
              size={size}
              iconSize={iconSize}
              iconProps={iconProps}
              strokeWidth={strokeWidth}
              className={className}
            />
            <div className="flex flex-1 items-center justify-end space-x-2">
              {showToggleText && (
                <span className="text-xs">{isOpen ? "Close" : "Expand"}</span>
              )}
              <CaretSortIcon className="h-4 w-4" strokeWidth={2.5} />
              <span className="sr-only">Toggle</span>
            </div>
          </Button>
        </CollapsibleTrigger>
      </div>
      <CollapsibleContent>{children}</CollapsibleContent>
    </Collapsible>
  )
}

export { CollapsibleSection, type CollapsibleSectionProps }
