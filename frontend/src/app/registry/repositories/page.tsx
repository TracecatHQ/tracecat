"use client"

import { useState } from "react"
import { RefreshCcw } from "lucide-react"

import { useRegistryRepositories } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import { ConfirmationDialog } from "@/components/confirmation-dialog"
import { RegistryRepositoriesTable } from "@/components/registry/registry-repos-table"

export default function RegistryRepositoriesPage() {
  const { repos, syncRepo } = useRegistryRepositories()
  const [syncing, setSyncing] = useState(false)
  const handleSyncRepositories = async () => {
    if (!repos) return
    try {
      // Sync all repositories and get their results
      setSyncing(true)
      const results = await Promise.allSettled(
        repos.map((repo) => syncRepo({ repositoryId: repo.id }))
      )

      // Filter out the failed promises
      const failures = results.filter(
        (result): result is PromiseRejectedResult =>
          result.status === "rejected"
      )

      if (failures.length > 0) {
        // Some repositories failed to sync
        toast({
          title: "Partial sync failure",
          description: `Couldn't sync ${failures.map((f) => f.reason.body?.detail || f.reason.message).join(", ")}`,
        })
      } else {
        // All repositories synced successfully
        toast({
          title: "Repositories synced",
          description: `Successfully synced ${results.length} repositories`,
        })
      }
    } catch (error) {
      // This catch block will only trigger for errors in the Promise.allSettled handling itself
      toast({
        title: "Failed to sync repositories",
        description: "An unexpected error occurred while syncing repositories",
        variant: "destructive",
      })
    } finally {
      setSyncing(false)
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
            <p className="text-md text-muted-foreground">
              View your organization&apos;s action repositories here.
            </p>
          </div>
          <div className="ml-auto flex items-center space-x-2">
            <ConfirmationDialog
              title="Sync All Repositories"
              description="Are you sure you want to sync all repositories? This will replace all existing actions with the latest from the repositories."
              onConfirm={handleSyncRepositories}
            >
              <Button
                role="combobox"
                variant="outline"
                className="items-center space-x-2"
                disabled={syncing}
              >
                <RefreshCcw className="size-4 text-muted-foreground/80" />
                <span>Sync All Repositories</span>
              </Button>
            </ConfirmationDialog>
          </div>
        </div>
        <RegistryRepositoriesTable />
      </div>
    </div>
  )
}
