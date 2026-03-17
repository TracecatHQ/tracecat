"use client"

import { ArrowUpRight, Lock } from "lucide-react"
import type { ReactNode } from "react"

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
  "Get production-ready automations with enterprise agents, metrics, and premium workflow tools.",
  "Access RBAC, SLAs, governance, and features built for production environments.",
]

interface LockedFeatureModalProps {
  children?: ReactNode
  open?: boolean
  onOpenChange?: (open: boolean) => void
}

export function LockedFeatureModal({
  children,
  open,
  onOpenChange,
}: LockedFeatureModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {children ? <DialogTrigger asChild>{children}</DialogTrigger> : null}
      <DialogContent
        title="Upgrade to unlock this feature"
        className="max-w-sm gap-0 overflow-hidden border-border p-0 shadow-none"
      >
        <DialogHeader className="space-y-2 border-b px-5 py-5 text-left">
          <div className="flex items-center gap-2">
            <div className="flex size-7 items-center justify-center rounded-md border bg-muted/40">
              <Lock className="size-3.5 text-muted-foreground" />
            </div>
            <DialogTitle className="text-base font-semibold">
              Upgrade to unlock this feature
            </DialogTitle>
          </div>
          <DialogDescription className="text-sm">
            Upgrade for enterprise agents, metrics, and other advanced features.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 px-5 py-4">
          <ul className="space-y-2 pl-4 text-sm text-muted-foreground">
            {LOCKED_FEATURE_BULLETS.map((bullet) => (
              <li key={bullet} className="list-disc">
                {bullet}
              </li>
            ))}
          </ul>
        </div>

        <DialogFooter className="border-t px-5 py-4 sm:justify-start sm:space-x-0">
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
        </DialogFooter>
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
