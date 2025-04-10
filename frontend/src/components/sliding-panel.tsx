import * as React from "react"

import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"

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
      <SheetTitle className="sr-only">Sliding Panel</SheetTitle>
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
