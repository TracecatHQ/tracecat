import { ActivityIcon } from "lucide-react"

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
          <ActivityIcon />
        </EmptyMedia>
        <EmptyTitle>No activity yet</EmptyTitle>
        <EmptyDescription>
          Agent runs will appear here when workflows execute.
        </EmptyDescription>
      </EmptyHeader>
    </Empty>
  )
}
