"use client"

import { RefreshCwIcon } from "lucide-react"
import { PlatformRegistryReposTable } from "@/components/admin/platform-registry-repos-table"
import { PlatformRegistryStatus } from "@/components/admin/platform-registry-status"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import { useAdminRegistryStatus, useAdminRegistrySync } from "@/hooks/use-admin"

export default function AdminRegistryPage() {
  const { status, isLoading, refetch } = useAdminRegistryStatus()
  const { syncAllRepositories, syncAllPending } = useAdminRegistrySync()

  const handleSyncAll = async () => {
    try {
      await syncAllRepositories(false)
      toast({
        title: "Sync complete",
        description: "All repositories have been synced successfully.",
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

  return (
    <div className="size-full overflow-auto">
      <div className="container flex h-full max-w-[1000px] flex-col space-y-12">
        <div className="flex w-full">
          <div className="items-start space-y-3 text-left">
            <h2 className="text-2xl font-semibold tracking-tight">
              Repositories
            </h2>
            <p className="text-base text-muted-foreground">
              Platform registry repositories and their sync status.
            </p>
          </div>
          <div className="ml-auto flex items-center space-x-2">
            <Button size="sm" onClick={handleSyncAll} disabled={syncAllPending}>
              <RefreshCwIcon
                className={`mr-2 size-4 ${syncAllPending ? "animate-spin" : ""}`}
              />
              {syncAllPending ? "Syncing..." : "Sync all"}
            </Button>
          </div>
        </div>

        <PlatformRegistryStatus status={status} isLoading={isLoading} />
        <PlatformRegistryReposTable />
      </div>
    </div>
  )
}
