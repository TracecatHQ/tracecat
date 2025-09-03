"use client"

import {
  AlertTriangleIcon,
  CheckCircleIcon,
  RefreshCcw,
  XCircleIcon,
} from "lucide-react"
import { useEffect, useState } from "react"
import type { PullResult } from "@/client"
import { CommitSelector } from "@/components/registry/commit-selector"
import { Alert, AlertDescription } from "@/components/ui/alert"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { toast } from "@/components/ui/use-toast"
import {
  useRepositoryCommits,
  useWorkflowSync,
} from "@/hooks/use-workspace-sync"
import { getRelativeTime } from "@/lib/event-history"

interface WorkflowPullDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  workspaceId: string
  gitRepoUrl?: string
  onPullSuccess?: () => void
}

export function WorkflowPullDialog({
  open,
  onOpenChange,
  workspaceId,
  gitRepoUrl,
  onPullSuccess,
}: WorkflowPullDialogProps) {
  const [selectedCommitSha, setSelectedCommitSha] = useState<string | null>(
    null
  )
  const [pullResult, setPullResult] = useState<PullResult | null>(null)

  // Use hooks for workflow sync operations
  const { pullWorkflows, pullWorkflowsIsPending } = useWorkflowSync(workspaceId)

  // Fetch commits for the repository
  const {
    commits,
    commitsIsLoading: commitsLoading,
    commitsError,
  } = useRepositoryCommits(workspaceId, { enabled: open })

  // Auto-select HEAD commit when commits are loaded
  useEffect(() => {
    if (commits?.length && !selectedCommitSha) {
      setSelectedCommitSha(commits[0].sha)
    }
  }, [commits, selectedCommitSha])

  const handleClose = () => {
    setPullResult(null)
    setSelectedCommitSha(null)
    onOpenChange(false)
  }

  const handlePull = async () => {
    if (!selectedCommitSha) return

    setPullResult(null)

    try {
      const pullOptions = {
        commit_sha: selectedCommitSha,
      }

      const result = await pullWorkflows(pullOptions)
      setPullResult(result)

      if (result.success) {
        toast({
          title: "Workflow pull completed",
          description: result.message,
        })
        onPullSuccess?.()
      } else {
        toast({
          title: "Workflow pull failed",
          description: result.message,
          variant: "destructive",
        })
      }
    } catch (error) {
      console.error("Error pulling workflows:", error)
      toast({
        title: "Pull operation failed",
        description:
          "An error occurred while pulling workflows from the repository.",
        variant: "destructive",
      })
    }
  }

  if (!gitRepoUrl) {
    return (
      <Dialog open={open} onOpenChange={onOpenChange}>
        <DialogContent className="max-w-2xl">
          <DialogHeader>
            <DialogTitle>Pull workflows from Git</DialogTitle>
            <DialogDescription>
              Configure a Git repository URL in workspace settings to enable
              workflow sync.
            </DialogDescription>
          </DialogHeader>
        </DialogContent>
      </Dialog>
    )
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-3xl max-h-[80vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Pull workflows from Git</DialogTitle>
          <DialogDescription>
            Select a commit to pull workflow definitions from:{" "}
            <span className="font-mono text-xs">{gitRepoUrl}</span>
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-6">
          {/* Commit Selection */}
          <div className="space-y-4">
            <div>
              <Label className="text-sm font-medium">Select commit</Label>
              <p className="text-xs text-muted-foreground mb-3">
                Choose which commit to pull workflow definitions from
              </p>
            </div>

            <CommitSelector
              commits={commits}
              currentCommitSha={selectedCommitSha}
              isLoading={commitsLoading}
              error={commitsError}
              onSelectCommit={setSelectedCommitSha}
              disabled={pullWorkflowsIsPending}
            />

            {/* Selected Commit Details */}
            {commitsLoading ? (
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
                <Skeleton className="h-4 w-3/4" />
                <Skeleton className="h-3 w-1/2" />
              </div>
            ) : (
              (() => {
                const effectiveCommitSha =
                  selectedCommitSha || commits?.[0]?.sha
                if (!effectiveCommitSha || !commits) return null

                const selectedCommit = commits.find(
                  (c) => c.sha === effectiveCommitSha
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
                      <p className="text-sm font-medium">
                        {selectedCommit.message}
                      </p>
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
                    </div>
                  </div>
                )
              })()
            )}
          </div>

          {/* Pull Results */}
          {pullResult && (
            <div className="space-y-4">
              <div className="flex items-center space-x-2">
                {pullResult.success ? (
                  <CheckCircleIcon className="size-5 text-green-500" />
                ) : (
                  <XCircleIcon className="size-5 text-red-500" />
                )}
                <h4 className="font-medium text-sm">
                  {pullResult.success ? "Pull completed" : "Pull failed"}
                </h4>
              </div>

              <div className="rounded-lg border p-4 space-y-3">
                <div className="grid grid-cols-3 gap-4 text-sm">
                  <div>
                    <span className="text-muted-foreground">Found:</span>
                    <span className="ml-2 font-medium">
                      {pullResult.workflows_found}
                    </span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Imported:</span>
                    <span className="ml-2 font-medium text-green-600">
                      {pullResult.workflows_imported}
                    </span>
                  </div>
                  <div>
                    <span className="text-muted-foreground">Issues:</span>
                    <span className="ml-2 font-medium text-amber-600">
                      {pullResult.diagnostics.length}
                    </span>
                  </div>
                </div>

                <p className="text-sm">{pullResult.message}</p>

                {/* Diagnostics */}
                {pullResult.diagnostics.length > 0 && (
                  <div className="space-y-2">
                    <h5 className="text-sm font-medium">Issues found:</h5>
                    <div className="space-y-2 max-h-32 overflow-y-auto">
                      {pullResult.diagnostics.map((diagnostic, index) => (
                        <div
                          key={
                            diagnostic.workflow_title ||
                            diagnostic.workflow_path
                          }
                          className="flex items-start space-x-2 text-xs p-2 bg-muted rounded"
                        >
                          <AlertTriangleIcon className="size-3 text-amber-500 mt-0.5 flex-shrink-0" />
                          <div className="space-y-1 min-w-0">
                            <div className="font-medium">
                              {diagnostic.workflow_title ||
                                diagnostic.workflow_path}
                            </div>
                            <div className="text-muted-foreground">
                              {diagnostic.message}
                            </div>
                            <Badge variant="outline" className="text-xs">
                              {diagnostic.error_type}
                            </Badge>
                          </div>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}
          <Alert variant="warning">
            <div className="flex items-center space-x-2">
              <AlertTriangleIcon className="size-3 text-amber-500 mt-0.5 flex-shrink-0" />
              <AlertDescription>
                This will overwrite any existing workflows, schedules, and
                configurations with the same ID.
              </AlertDescription>
            </div>
          </Alert>
          {/* Action Buttons */}
          <div className="flex justify-end space-x-3 pt-4">
            <Button
              variant="outline"
              onClick={handleClose}
              disabled={pullWorkflowsIsPending}
            >
              {pullResult ? "Close" : "Cancel"}
            </Button>
            {!pullResult && (
              <Button
                onClick={handlePull}
                disabled={
                  pullWorkflowsIsPending || !selectedCommitSha || commitsLoading
                }
              >
                <div className="flex items-center space-x-2">
                  <RefreshCcw
                    className={`size-4 ${pullWorkflowsIsPending ? "animate-spin" : ""}`}
                  />
                  <span>
                    {pullWorkflowsIsPending ? "Pulling..." : "Pull workflows"}
                  </span>
                </div>
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  )
}
