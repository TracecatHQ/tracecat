import { InboxIcon } from "lucide-react"

import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty"

export function InboxEmptyState() {
  return (
    <Empty className="h-full border-none">
      <EmptyHeader>
        <EmptyMedia variant="icon">
          <InboxIcon />
        </EmptyMedia>
        <EmptyTitle>No pending items</EmptyTitle>
        <EmptyDescription>
          Items will appear here when agents need approval.
        </EmptyDescription>
      </EmptyHeader>
    </Empty>
  )
}
