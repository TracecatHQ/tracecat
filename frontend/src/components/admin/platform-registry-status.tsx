"use client"

import { RefreshCwIcon } from "lucide-react"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card"
import { toast } from "@/components/ui/use-toast"
import { useAdminRegistryStatus, useAdminRegistrySync } from "@/hooks/use-admin"
import { getRelativeTime } from "@/lib/event-history"

export function PlatformRegistryStatus() {
  const { status, isLoading, refetch } = useAdminRegistryStatus()
  const { syncAllRepositories, syncAllPending } = useAdminRegistrySync()

  const handleSyncAll = async () => {
    try {
      await syncAllRepositories(false)
      toast({
        title: "Sync started",
        description: "All repositories are being synced.",
      })
      refetch()
    } catch (error) {
      console.error("Failed to sync repositories", error)
      toast({
        title: "Sync failed",
        description: "Failed to sync repositories. Please try again.",
        variant: "destructive",
      })
    }
  }

  if (isLoading) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Registry status</CardTitle>
        </CardHeader>
        <CardContent>
          <div className="text-muted-foreground">Loading...</div>
        </CardContent>
      </Card>
    )
  }

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle>Registry status</CardTitle>
            <CardDescription>
              Platform-wide registry sync status and health.
            </CardDescription>
          </div>
          <Button size="sm" onClick={handleSyncAll} disabled={syncAllPending}>
            <RefreshCwIcon
              className={`mr-2 size-4 ${syncAllPending ? "animate-spin" : ""}`}
            />
            {syncAllPending ? "Syncing..." : "Sync all"}
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-3 gap-4">
          <div>
            <div className="text-sm text-muted-foreground">
              Total repositories
            </div>
            <div className="text-2xl font-semibold">
              {status?.total_repositories ?? 0}
            </div>
          </div>
          <div>
            <div className="text-sm text-muted-foreground">Last sync</div>
            <div className="text-lg">
              {status?.last_sync_at
                ? getRelativeTime(new Date(status.last_sync_at))
                : "Never"}
            </div>
          </div>
          <div>
            <div className="text-sm text-muted-foreground">Status</div>
            <Badge
              variant={status?.repositories?.length ? "default" : "secondary"}
              className={
                status?.repositories?.length
                  ? "bg-green-500 hover:bg-green-600"
                  : ""
              }
            >
              {status?.repositories?.length ? "Active" : "No repositories"}
            </Badge>
          </div>
        </div>
      </CardContent>
    </Card>
  )
}
