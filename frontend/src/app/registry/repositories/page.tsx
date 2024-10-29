"use client"

import { RefreshCcw } from "lucide-react"

import { useRegistryRepositories } from "@/lib/hooks"
import { Button } from "@/components/ui/button"
import { toast } from "@/components/ui/use-toast"
import { ConfirmationDialog } from "@/components/confirmation-dialog"
import { RegistryRepositoriesTable } from "@/components/registry/registry-repos-table"

export default function RegistryRepositoriesPage() {
  const { syncRepos, syncReposIsPending } = useRegistryRepositories()
  const handleSyncRepositories = async () => {
    try {
      // Sync all repositories
      await syncRepos({})
      toast({
        title: "Repositories synced",
        description: "Your repositories have been synced",
      })
    } catch (error) {
      toast({
        title: "Failed to sync repositories",
        description: "An error occurred while syncing your repositories",
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
                disabled={syncReposIsPending}
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
