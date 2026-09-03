"use client"

import { ArrowUpRight, Lock } from "lucide-react"
import type { ReactElement, ReactNode } from "react"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog"
import { cn } from "@/lib/utils"

const LOCKED_FEATURE_BULLETS = [
  "Get production-ready automations with enterprise agents, reusable skills, metrics, and premium workflow tools.",
  "Author, version, and publish skills to share agent behavior across your workspace.",
  "Access RBAC, SLAs, governance, and features built for production environments.",
]

interface LockedFeatureModalProps {
  children?: ReactElement
  open?: boolean
  onOpenChange?: (open: boolean) => void
  title?: string
  description?: ReactNode
  bullets?: string[]
  footer?: ReactNode
  hideFooter?: boolean
}

export function LockedFeatureModal({
  children,
  open,
  onOpenChange,
  title = "Upgrade to unlock this feature",
  description = "Upgrade for enterprise agents, skills, metrics, and other advanced features.",
  bullets = LOCKED_FEATURE_BULLETS,
  footer,
  hideFooter = false,
}: LockedFeatureModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {children ? <DialogTrigger asChild>{children}</DialogTrigger> : null}
      <DialogContent
        title={title}
        className="max-w-sm gap-0 overflow-hidden border-border p-0 shadow-none"
      >
        <DialogHeader className="space-y-2 border-b px-5 py-5 text-left">
          <div className="flex items-center gap-2">
            <div className="flex size-7 items-center justify-center rounded-md border bg-muted/40">
              <Lock className="size-3.5 text-muted-foreground" />
            </div>
            <DialogTitle className="text-base font-semibold">
              {title}
            </DialogTitle>
          </div>
          <DialogDescription className="text-sm">
            {description}
          </DialogDescription>
        </DialogHeader>

        {bullets.length > 0 ? (
          <div className="space-y-4 px-5 py-4">
            <ul className="space-y-2 pl-4 text-sm text-muted-foreground">
              {bullets.map((bullet, index) => (
                <li key={`bullet-${index}`} className="list-disc">
                  {bullet}
                </li>
              ))}
            </ul>
          </div>
        ) : null}

        {hideFooter ? null : (
          <DialogFooter className="border-t px-5 py-4 sm:justify-start sm:space-x-0">
            {footer ?? (
              <Button
                variant="outline"
                size="sm"
                asChild
                className="w-full justify-center"
              >
                <a
                  href="https://tracecat.com"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  Learn more <ArrowUpRight className="size-4" />
                </a>
              </Button>
            )}
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  )
}

export function LockedFeatureChip({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 translate-y-px items-center justify-center text-muted-foreground",
        className
      )}
    >
      <Lock className="size-3 text-muted-foreground" />
    </span>
  )
}
