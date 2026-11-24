"use client"

import { X } from "lucide-react"
import type { ReactNode } from "react"
import { ProviderIcon } from "@/components/icons"
import { Button } from "@/components/ui/button"

/**
 * The Channels page's right-hand sheet, beside the table: managing a bot and
 * adding one both open here.
 */
export function ChannelsSideSheet({
  label,
  providerId,
  title,
  badge,
  onClose,
  closeDisabled = false,
  footer,
  children,
}: {
  /** The sheet's accessible name. */
  label: string
  providerId: string
  title: string
  badge?: ReactNode
  onClose: () => void
  /** A request is running; closing would drop its callbacks. */
  closeDisabled?: boolean
  /** Actions pinned under the body. */
  footer?: ReactNode
  children: ReactNode
}) {
  return (
    <aside
      aria-label={label}
      className="flex h-full w-[380px] shrink-0 flex-col border-l bg-background"
    >
      <div className="flex items-center justify-between gap-2 px-5 pb-2 pt-5">
        <div className="flex min-w-0 items-center gap-2">
          <ProviderIcon
            providerId={providerId}
            className="size-[18px] shrink-0 p-0"
          />
          <h3 className="truncate text-sm font-semibold">{title}</h3>
          {badge}
        </div>
        <Button
          type="button"
          size="icon"
          variant="ghost"
          className="size-7"
          aria-label="Close"
          onClick={onClose}
          disabled={closeDisabled}
        >
          <X className="size-4" />
        </Button>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-5 pt-3">
        {children}
      </div>
      {footer ? (
        <div className="flex items-center justify-end gap-2 border-t px-5 py-3">
          {footer}
        </div>
      ) : null}
    </aside>
  )
}
