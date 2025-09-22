"use client"

import { RefreshCcw } from "lucide-react"
import { useEffect, useState } from "react"
import type { RegistryRepositoryReadMinimal } from "@/client"
import { CommitSelector } from "@/components/registry/commit-selector"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "@/components/ui/use-toast"
import { getRelativeTime } from "@/lib/event-history"
import { useRegistryRepositories, useRepositoryCommits } from "@/lib/hooks"

interface CommitSelectorDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  selectedRepo: RegistryRepositoryReadMinimal | null
  initialCommitSha?: string | null
}

export function CommitSelectorDialog({
  open,
  onOpenChange,
  selectedRepo,
  initialCommitSha,
}: CommitSelectorDialogProps) {
  const [selectedCommitSha, setSelectedCommitSha] = useState<string | null>(
    initialCommitSha || null
  )

  const { syncRepo, syncRepoIsPending } = useRegistryRepositories()

  // Fetch commits for the selected repository
  const { commits, commitsIsLoading, commitsError } = useRepositoryCommits(
    selectedRepo?.id || null,
    { enabled: open }
  )

  // Auto-select HEAD commit when commits are loaded and no commit is selected
  useEffect(() => {
    if (commits?.length && !selectedCommitSha && !initialCommitSha) {
      setSelectedCommitSha(commits[0].sha)
    }
  }, [commits, selectedCommitSha, initialCommitSha])

  const handleClose = () => {
    setSelectedCommitSha(null)
    onOpenChange(false)
  }

  const handleSync = async () => {
    if (!selectedRepo || !selectedCommitSha) return

    try {
      await syncRepo({
        repositoryId: selectedRepo.id,
        requestBody: {
          target_commit_sha: selectedCommitSha,
        },
      })
      handleClose()
      toast({
        title: "Successfully synced repository",
        description: (
          <span className="flex flex-col space-y-2">
            <span>
              Successfully synced{" "}
              <b className="inline-block">{selectedRepo.origin}</b>
            </span>
            <span className="text-xs">
              to commit {selectedCommitSha.substring(0, 7)}
            </span>
          </span>
        ),
      })
    } catch (error) {
      console.error("Error syncing repository to specific commit", error)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-2xl">
        <DialogHeader>
          <DialogTitle>Select commit to sync</DialogTitle>
        </DialogHeader>
        <div className="space-y-4">
          {selectedRepo && (
            <div>
              <p className="text-sm text-muted-foreground mb-4">
                Select a commit to sync repository:{" "}
                <span className="font-mono">{selectedRepo.origin}</span>
              </p>
              <div className="space-y-4">
                <CommitSelector
                  commits={commits}
                  currentCommitSha={selectedCommitSha}
                  isLoading={commitsIsLoading}
                  error={commitsError}
                  onSelectCommit={(commitSha) => {
                    setSelectedCommitSha(commitSha)
                  }}
                />

                {/* Selected Commit Details */}
                {commitsIsLoading ? (
                  <div className="rounded-lg border bg-muted/50 p-4 space-y-3">
                    <div className="flex items-center justify-between">
                      <h4 className="font-medium text-sm">
                        Selected commit details
                      </h4>
                      <div className="flex items-center space-x-2">
                        <Skeleton className="h-5 w-16" />
                        <Skeleton className="h-5 w-12" />
                      </div>
                    </div>
                    <div className="space-y-2">
                      <div>
                        <Skeleton className="h-4 w-3/4" />
                      </div>
                      <div className="flex items-center justify-between">
                        <Skeleton className="h-3 w-1/2" />
                        <div className="flex items-center space-x-2">
                          <Skeleton className="h-3 w-20" />
                          <Skeleton className="h-3 w-16" />
                        </div>
                      </div>
                      <div className="pt-1">
                        <Skeleton className="h-3 w-full" />
                      </div>
                    </div>
                  </div>
                ) : (
                  (() => {
                    const effectiveCommitSha =
                      selectedCommitSha || commits?.[0]?.sha
                    if (!effectiveCommitSha || !commits) return null

                    const selectedCommit = commits.find(
                      (commit) => commit.sha === effectiveCommitSha
                    )
                    if (!selectedCommit) return null

                    const commitDate = new Date(selectedCommit.date)
                    const relativeTime = getRelativeTime(commitDate)
                    const isHead = commits[0]?.sha === effectiveCommitSha

                    return (
                      <div className="rounded-lg border bg-muted/50 p-4 space-y-3">
                        <div className="flex items-center justify-between">
                          <h4 className="font-medium text-sm">
                            Selected commit details
                          </h4>
                          <div className="flex items-center space-x-2">
                            <Badge
                              variant="secondary"
                              className="font-mono text-xs"
                            >
                              {selectedCommit.sha.substring(0, 7)}
                            </Badge>
                            {isHead && (
                              <Badge variant="default" className="text-xs">
                                HEAD
                              </Badge>
                            )}
                          </div>
                        </div>
                        <div className="space-y-2">
                          <div>
                            <p className="text-sm font-medium text-foreground">
                              {selectedCommit.message}
                            </p>
                          </div>
                          <div className="flex items-center justify-between text-xs text-muted-foreground">
                            <span>
                              by {selectedCommit.author} (
                              {selectedCommit.author_email})
                            </span>
                            <div className="flex items-center space-x-2">
                              <span>{commitDate.toLocaleDateString()}</span>
                              <span>•</span>
                              <span>{relativeTime}</span>
                            </div>
                          </div>
                          <div className="pt-1">
                            <span className="text-xs text-muted-foreground font-mono">
                              Full SHA: {selectedCommit.sha}
                            </span>
                          </div>
                        </div>
                      </div>
                    )
                  })()
                )}
              </div>
            </div>
          )}
          <div className="flex justify-end space-x-2 pt-4">
            <Button variant="outline" onClick={handleClose}>
              Cancel
            </Button>
            <Button
              onClick={handleSync}
              disabled={syncRepoIsPending || !selectedCommitSha}
            >
              <div className="flex items-center space-x-2">
                <RefreshCcw
                  className={`size-4 ${syncRepoIsPending ? "animate-spin" : ""}`}
                />
                <span>{syncRepoIsPending ? "Syncing..." : "Sync"}</span>
              </div>
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
