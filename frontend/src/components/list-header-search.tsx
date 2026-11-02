"use client"

import { SearchIcon } from "lucide-react"
import {
  type ComponentPropsWithoutRef,
  forwardRef,
  type ReactNode,
} from "react"
import { Input } from "@/components/ui/input"
import { cn } from "@/lib/utils"

export interface ListHeaderSearchProps
  extends Omit<ComponentPropsWithoutRef<typeof Input>, "type"> {
  /** Controls rendered right after the input, e.g. a search mode select. */
  children?: ReactNode
  containerClassName?: string
}

/**
 * Borderless search input for the `h-10` header row of list pages. It fills
 * the space left of the header's right-aligned controls.
 */
export const ListHeaderSearch = forwardRef<
  HTMLInputElement,
  ListHeaderSearchProps
>(function ListHeaderSearch(
  { children, className, containerClassName, ...props },
  ref
) {
  return (
    <div
      className={cn(
        "flex min-w-0 flex-1 items-center gap-3",
        containerClassName
      )}
    >
      <label className="flex min-w-0 flex-1 cursor-text items-center gap-3">
        <span className="flex size-7 shrink-0 items-center justify-center">
          <SearchIcon className="size-4 text-muted-foreground" />
        </span>
        <Input
          ref={ref}
          type="text"
          className={cn(
            "h-7 w-full min-w-0 flex-1 border-none bg-transparent p-0",
            "text-sm",
            "shadow-none outline-none",
            "placeholder:text-muted-foreground",
            "focus-visible:ring-0 focus-visible:ring-offset-0",
            className
          )}
          {...props}
        />
      </label>
      {children}
    </div>
  )
})
