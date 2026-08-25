"use client"

import { RefreshCcw } from "lucide-react"
import type {
  GitCommitInfo,
  RegistryRepositoriesSyncRegistryRepositoryData,
  RegistryRepositoryReadMinimal,
  tracecat__registry__repositories__schemas__RegistrySyncResponse,
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
import { getRelativeTime } from "@/lib/event-history"

/** Props for {@link SyncRepositoryDialog}. */
export interface SyncRepositoryDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedRepo: RegistryRepositoryReadMinimal | null
  setSelectedRepo?: (repo: RegistryRepositoryReadMinimal | null) => void
  syncRepo: (
    params: RegistryRepositoriesSyncRegistryRepositoryData
  ) => Promise<tracecat__registry__repositories__schemas__RegistrySyncResponse>
  syncRepoIsPending: boolean
  /** Sync this commit instead of the remote HEAD. */
  targetCommit?: GitCommitInfo | null
  /** SHA of the current version; defaults to the repository's last synced SHA. */
  currentCommitSha?: string | null
}

/** Confirmation dialog for syncing a registry repository from its remote. */
export function SyncRepositoryDialog({
  open,
  onOpenChange,
  selectedRepo,
  setSelectedRepo,
  syncRepo,
  syncRepoIsPending,
  targetCommit,
  currentCommitSha,
}: SyncRepositoryDialogProps) {
  const shortSha = targetCommit?.sha.substring(0, 7)
  const currentSha = currentCommitSha ?? selectedRepo?.commit_sha

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
      await syncRepo({
        repositoryId: selectedRepo.id,
        requestBody: targetCommit
          ? { target_commit_sha: targetCommit.sha }
          : undefined,
      })
      toast({
        title: "Successfully synced repository",
        description: (
          <span className="flex flex-col space-y-2">
            <span>
              Successfully reloaded actions from{" "}
              <b className="inline-block">{selectedRepo.origin}</b>
            </span>
            {shortSha && <span className="text-xs">at commit {shortSha}</span>}
          </span>
        ),
      })
    } catch (error) {
      console.error("Error syncing repository", error)
    } finally {
      setSelectedRepo?.(null)
    }
  }

  return (
    <AlertDialog open={open} onOpenChange={onOpenChange}>
      <AlertDialogContent className="max-w-2xl">
        <AlertDialogHeader>
          <AlertDialogTitle>
            {shortSha ? `Sync commit ${shortSha}` : "Sync repository"}
          </AlertDialogTitle>
          <AlertDialogDescription>
            <span className="flex flex-col space-y-3">
              <span>
                {targetCommit
                  ? "You are about to sync the repository at this commit."
                  : "You are about to pull the latest version of the repository."}
              </span>
              <span className="max-w-full rounded-md border px-3 py-2 font-mono text-sm font-semibold tracking-tight text-foreground break-all whitespace-normal">
                {selectedRepo?.origin}
              </span>
              {targetCommit && (
                <span className="flex flex-col gap-1 rounded-md border px-3 py-2 text-sm">
                  <span className="text-foreground">
                    {targetCommit.message.split("\n")[0]}
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {targetCommit.author} ·{" "}
                    {getRelativeTime(new Date(targetCommit.date))}
                  </span>
                </span>
              )}
              {currentSha && (
                <span className="flex flex-wrap items-start gap-2 text-sm text-muted-foreground">
                  <span>Current SHA:</span>
                  <span className="rounded bg-secondary px-2 py-1 font-mono text-xs text-secondary-foreground break-all whitespace-normal">
                    {currentSha}
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
                {targetCommit
                  ? "Are you sure you want to proceed? This will reload all existing actions with the versions from this commit."
                  : "Are you sure you want to proceed? This will reload all existing actions with the latest versions from the remote repository."}
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
