"use client"

import { ChevronDownIcon, MousePointerClickIcon } from "lucide-react"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { cn } from "@/lib/utils"

/**
 * Install or uninstall a library entry, with a menu to start an agent from it.
 *
 * @param props The primary action, and the create-agent action when allowed.
 * @returns A split button, or a plain button without create access.
 */
export function SkillLibraryActionButton({
  label,
  onAction,
  onCreateAgent,
  createAgentLabel,
  disabled,
  className,
}: {
  label: string
  onAction: () => void
  /** Omitted when the viewer cannot create agents. */
  onCreateAgent?: () => void
  createAgentLabel: string
  disabled: boolean
  className?: string
}) {
  const primary = (
    <Button
      type="button"
      size="sm"
      variant="outline"
      className={className}
      disabled={disabled}
      onClick={onAction}
    >
      {label}
    </Button>
  )
  if (!onCreateAgent) return primary
  // One bordered control with a light inset divider, not two joined buttons.
  return (
    <div className="inline-flex h-8 shrink-0 items-stretch rounded-md border bg-background">
      <Button
        type="button"
        size="sm"
        variant="ghost"
        className={cn("h-full rounded-r-none", className)}
        disabled={disabled}
        onClick={onAction}
      >
        {label}
      </Button>
      <span aria-hidden="true" className="my-1.5 w-px bg-border" />
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            className="h-full rounded-l-none px-1.5"
            disabled={disabled}
            aria-label="More actions"
          >
            <ChevronDownIcon className="size-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem onSelect={onCreateAgent}>
            <MousePointerClickIcon className="mr-2 size-4" />
            {createAgentLabel}
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  )
}
