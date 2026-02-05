"use client"

import type { AdminRegistryGetRegistryStatusResponse } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Item } from "@/components/ui/item"
import { getRelativeTime } from "@/lib/event-history"

type PlatformRegistryStatusProps = {
  status?: AdminRegistryGetRegistryStatusResponse
  isLoading: boolean
}

export function PlatformRegistryStatus({
  status,
  isLoading,
}: PlatformRegistryStatusProps) {
  if (isLoading) {
    return <div className="text-muted-foreground">Loading...</div>
  }

  return (
    <Item variant="outline" className="p-6">
      <div className="flex w-full items-start justify-between gap-6">
        <div className="flex-1">
          <div className="text-sm text-muted-foreground">
            Total repositories
          </div>
          <div className="text-2xl font-semibold">
            {status?.total_repositories ?? 0}
          </div>
        </div>
        <div className="flex-1">
          <div className="text-sm text-muted-foreground">Last sync</div>
          <div className="text-lg">
            {status?.last_sync_at
              ? getRelativeTime(new Date(status.last_sync_at))
              : "Never"}
          </div>
        </div>
        <div className="flex-1">
          <div className="text-sm text-muted-foreground">Status</div>
          <Badge
            variant={status?.repositories?.length ? "default" : "secondary"}
          >
            {status?.repositories?.length ? "Active" : "No repositories"}
          </Badge>
        </div>
      </div>
    </Item>
  )
}
