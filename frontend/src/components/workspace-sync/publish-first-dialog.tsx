"use client"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { getExportError } from "@/components/workspace-sync/export-error"

/** Only the server's explicit prerequisite code selects Publish-first guidance. */
export function isPublishRequired(error: unknown): boolean {
  return (
    getExportError(error)?.diagnostics?.some(
      (diagnostic) => diagnostic.code === "publish_required"
    ) ?? false
  )
}

/** Explain the first Publish prerequisite without publishing or pushing. */
export function PublishFirstDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="sm">
        <DialogHeader>
          <DialogTitle>Publish this workflow first</DialogTitle>
          <DialogDescription>
            Publish this workflow before pushing it to Git.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <DialogClose asChild>
            <Button>Got it</Button>
          </DialogClose>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
