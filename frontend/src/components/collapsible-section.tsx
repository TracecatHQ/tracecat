import { CaretSortIcon } from "@radix-ui/react-icons"
import type { LucideIcon } from "lucide-react"
import React from "react"
import DecoratedHeader, {
  type DecoratedHeaderProps,
} from "@/components/decorated-header"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"

interface CollapsibleSectionProps extends DecoratedHeaderProps {
  icon?: LucideIcon
  node: React.ReactNode
  showToggleText?: boolean
  defaultIsOpen?: boolean
  children: React.ReactNode
}

function CollapsibleSection({
  icon: Icon,
  node,
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
              node={node}
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
              <CaretSortIcon className="size-4" strokeWidth={2.5} />
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
