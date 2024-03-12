import * as React from "react"

import { Sheet, SheetContent } from "@/components/ui/sheet"

interface SlidingPanelProps
  extends React.PropsWithChildren<React.HTMLAttributes<HTMLDivElement>> {
  isOpen: boolean
  setIsOpen: (isOpen: boolean) => void
}

export function SlidingPanel({
  isOpen,
  setIsOpen,
  children,
  className,
}: SlidingPanelProps) {
  return (
    <Sheet modal={false} open={isOpen} onOpenChange={setIsOpen}>
      <SheetContent
        className={className}
        onOpenAutoFocus={(e) => {
          e.preventDefault()
        }} // Prevents the first focusable element from being focused on open
      >
        {children}
      </SheetContent>
    </Sheet>
  )
}
