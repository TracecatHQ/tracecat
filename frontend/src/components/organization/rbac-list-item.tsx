"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import { ChevronRightIcon } from "lucide-react"
import type { ReactNode } from "react"
import { Button } from "@/components/ui/button"
import {
  Collapsible,
  CollapsibleContent,
  CollapsibleTrigger,
} from "@/components/ui/collapsible"
import { cn } from "@/lib/utils"

interface RbacListItemProps {
  title: ReactNode
  subtitle?: ReactNode
  badges?: ReactNode
  icon?: ReactNode
  actions?: ReactNode
  children?: ReactNode
  isExpanded?: boolean
  onExpandedChange?: (expanded: boolean) => void
  className?: string
}

export function RbacListItem({
  title,
  subtitle,
  badges,
  icon,
  actions,
  children,
  isExpanded,
  onExpandedChange,
  className,
}: RbacListItemProps) {
  const hasExpandableContent = Boolean(children)

  return (
    <Collapsible open={isExpanded} onOpenChange={onExpandedChange}>
      <div
        className={cn(
          "group border-b border-border/50 last:border-b-0",
          className
        )}
      >
        <div className="flex items-center gap-3 px-3 py-2.5">
          {/* Expand chevron */}
          {hasExpandableContent ? (
            <CollapsibleTrigger asChild>
              <Button
                variant="ghost"
                size="sm"
                className="size-6 p-0 hover:bg-muted/50"
              >
                <ChevronRightIcon
                  className={cn(
                    "size-4 text-muted-foreground transition-transform duration-200",
                    isExpanded && "rotate-90"
                  )}
                />
              </Button>
            </CollapsibleTrigger>
          ) : (
            <div className="size-6" />
          )}

          {/* Icon */}
          {icon && (
            <div className="flex-shrink-0 text-muted-foreground">{icon}</div>
          )}

          {/* Main content - clickable to expand */}
          <CollapsibleTrigger asChild disabled={!hasExpandableContent}>
            <button
              type="button"
              className={cn(
                "flex min-w-0 flex-1 items-center gap-3 text-left",
                hasExpandableContent && "cursor-pointer"
              )}
            >
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium">{title}</span>
                  {badges}
                </div>
                {subtitle && (
                  <div className="truncate text-xs text-muted-foreground">
                    {subtitle}
                  </div>
                )}
              </div>
            </button>
          </CollapsibleTrigger>

          {/* Actions */}
          {actions && (
            <div
              className="flex-shrink-0"
              onClick={(e) => e.stopPropagation()}
              onKeyDown={(e) => e.stopPropagation()}
            >
              {actions}
            </div>
          )}
        </div>

        {/* Expanded content */}
        {hasExpandableContent && (
          <CollapsibleContent>
            <div className="border-t border-border/30 bg-muted/20 px-3 py-3 pl-12">
              {children}
            </div>
          </CollapsibleContent>
        )}
      </div>
    </Collapsible>
  )
}

interface RbacListContainerProps {
  children: ReactNode
  className?: string
}

export function RbacListContainer({
  children,
  className,
}: RbacListContainerProps) {
  return (
    <div
      className={cn(
        "rounded-md border border-border/60 bg-background",
        className
      )}
    >
      {children}
    </div>
  )
}

interface RbacListHeaderProps {
  left?: ReactNode
  right?: ReactNode
  className?: string
}

export function RbacListHeader({
  left,
  right,
  className,
}: RbacListHeaderProps) {
  return (
    <div
      className={cn("flex items-center justify-between gap-4 pb-4", className)}
    >
      {left && <div className="flex items-center gap-3">{left}</div>}
      {right && <div className="flex items-center gap-2">{right}</div>}
    </div>
  )
}

interface RbacListEmptyProps {
  message: string
  className?: string
}

export function RbacListEmpty({ message, className }: RbacListEmptyProps) {
  return (
    <div
      className={cn(
        "flex items-center justify-center py-12 text-sm text-muted-foreground",
        className
      )}
    >
      {message}
    </div>
  )
}

interface RbacActionMenuProps {
  children: ReactNode
}

export function RbacActionMenu({ children }: RbacActionMenuProps) {
  return (
    <Button
      variant="ghost"
      className="size-8 p-0 opacity-0 transition-opacity group-hover:opacity-100 data-[state=open]:opacity-100"
    >
      <span className="sr-only">Open menu</span>
      <DotsHorizontalIcon className="size-4" />
    </Button>
  )
}

interface RbacDetailRowProps {
  label: string
  children: ReactNode
  className?: string
}

export function RbacDetailRow({
  label,
  children,
  className,
}: RbacDetailRowProps) {
  return (
    <div className={cn("flex items-start gap-2 text-xs", className)}>
      <span className="w-24 flex-shrink-0 font-medium text-muted-foreground">
        {label}
      </span>
      <div className="flex-1">{children}</div>
    </div>
  )
}

interface RbacBadgeProps {
  children: ReactNode
  variant?: "default" | "preset" | "custom"
  className?: string
}

export function RbacBadge({
  children,
  variant = "default",
  className,
}: RbacBadgeProps) {
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-medium",
        variant === "preset" && "bg-muted text-muted-foreground",
        variant === "custom" && "bg-primary/10 text-primary",
        variant === "default" && "bg-secondary text-secondary-foreground",
        className
      )}
    >
      {children}
    </span>
  )
}
