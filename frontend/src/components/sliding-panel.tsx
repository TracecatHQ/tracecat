import type * as React from "react"
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet"
import { cn } from "@/lib/utils"

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
        className={cn("!animate-none !transition-none !duration-0", className)}
        onOpenAutoFocus={(e) => {
          e.preventDefault()
        }} // Prevents the first focusable element from being focused on open
      >
        {children}
      </SheetContent>
    </Sheet>
  )
}
