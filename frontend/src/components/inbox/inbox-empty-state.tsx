import { InboxIcon } from "lucide-react"

export function InboxEmptyState() {
  return (
    <div className="flex h-full items-center justify-center">
      <div className="text-center">
        <InboxIcon className="mx-auto size-12 text-muted-foreground/30" />
        <h3 className="mt-4 text-sm font-medium text-muted-foreground">
          No pending items
        </h3>
        <p className="mt-1 text-xs text-muted-foreground/60">
          Items will appear here when agents need approval.
        </p>
      </div>
    </div>
  )
}
