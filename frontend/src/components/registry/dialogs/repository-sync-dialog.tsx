"use client"

import { AlertTriangleIcon, RefreshCcw } from "lucide-react"
import type {
  RegistryRepositoriesSyncRegistryRepositoryData,
  RegistryRepositoryReadMinimal,
} from "@/client"
import { Spinner } from "@/components/loading/spinner"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { toast } from "@/components/ui/use-toast"

interface SyncRepositoryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedRepo: RegistryRepositoryReadMinimal | null
  setSelectedRepo: (repo: RegistryRepositoryReadMinimal | null) => void
  syncRepo: (
    params: RegistryRepositoriesSyncRegistryRepositoryData
  ) => Promise<void>
  syncRepoIsPending: boolean
}

export function SyncRepositoryDialog({
  open,
  onOpenChange,
  selectedRepo,
  setSelectedRepo,
  syncRepo,
  syncRepoIsPending,
}: SyncRepositoryDialogProps) {
  const handleSync = async () => {
    if (!selectedRepo) {
      console.error("No repository selected")
      return
    }

    try {
      toast({
        title: "Syncing repository",
        description: (
          <span className="flex flex-col space-y-2">
            <span className="flex items-center space-x-2">
              <Spinner className="size-3" />
              <span>
                Syncing repository{" "}
                <b className="inline-block">{selectedRepo.origin}</b>
              </span>
            </span>
          </span>
        ),
      })
      await syncRepo({ repositoryId: selectedRepo.id })
      toast({
        title: "Successfully synced repository",
        description: (
          <span className="flex flex-col space-y-2">
            <span>
              Successfully reloaded actions from{" "}
              <b className="inline-block">{selectedRepo.origin}</b>
            </span>
          </span>
        ),
      })
    } catch (error) {
      console.error("Error syncing repository", error)
      toast({
        title: "Error syncing repository",
        description: (
          <div className="flex items-start gap-2">
            <AlertTriangleIcon className="size-4 fill-rose-600 text-white" />
            <span>An error occurred while reloading the repository.</span>
          </div>
        ),
      })
    } finally {
      setSelectedRepo(null)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Sync repository</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="flex flex-col space-y-2">
              <span>
                You are about to pull the latest version of the repository{" "}
              </span>
              <b className="font-mono tracking-tighter">
                {selectedRepo?.origin}
              </b>
              {selectedRepo?.commit_sha && (
                <span className="text-sm text-muted-foreground">
                  <span>Current SHA: </span>
                  <span className="font-mono text-xs bg-secondary text-secondary-foreground px-2 py-1 rounded">
                    {selectedRepo.commit_sha}
                  </span>
                </span>
              )}
              {selectedRepo?.last_synced_at && (
                <span className="text-sm text-muted-foreground">
                  <span>Last synced: </span>
                  <span>
                    {new Date(selectedRepo.last_synced_at).toLocaleString()}
                  </span>
                </span>
              )}
              <span>
                Are you sure you want to proceed? This will reload all existing
                actions with the latest versions from the remote repository.
              </span>
            </span>
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction onClick={handleSync} disabled={syncRepoIsPending}>
            <div className="flex items-center space-x-2">
              <RefreshCcw
                className={`size-4 ${syncRepoIsPending ? "animate-spin" : ""}`}
              />
              <span>{syncRepoIsPending ? "Syncing..." : "Sync"}</span>
            </div>
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
