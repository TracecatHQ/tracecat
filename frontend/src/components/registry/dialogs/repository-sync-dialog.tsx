"use client"

import { RefreshCcw } from "lucide-react"
import type { RegistryRepositoryReadMinimal } from "@/client"
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
import { Badge } from "@/components/ui/badge"
import { toast } from "@/components/ui/use-toast"
import { useRegistryRepositories } from "@/lib/hooks"

interface SyncRepositoryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedRepo: RegistryRepositoryReadMinimal | null
  setSelectedRepo: (repo: RegistryRepositoryReadMinimal | null) => void
}

export function SyncRepositoryDialog({
  open,
  onOpenChange,
  selectedRepo,
  setSelectedRepo,
}: SyncRepositoryDialogProps) {
  const { syncRepo, syncRepoIsPending } = useRegistryRepositories()

  const handleSync = async () => {
    if (!selectedRepo) {
      console.error("No repository selected")
      return
    }

    try {
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
      console.error("Error reloading repository", error)
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
                  <Badge className="font-mono text-xs" variant="secondary">
                    {selectedRepo.commit_sha}
                  </Badge>
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
              <RefreshCcw className="size-4" />
              <span>Sync</span>
            </div>
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
