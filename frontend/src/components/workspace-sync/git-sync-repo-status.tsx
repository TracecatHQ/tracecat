"use client"

import { AlertTriangleIcon } from "lucide-react"
import type { VcsProvider } from "@/client"
import { VcsProviderIcon } from "@/components/organization/vcs-icons"
import { Button } from "@/components/ui/button"

interface GitSyncRepoStatusProps {
  provider: VcsProvider
  repoName: string
  branchesIsLoading: boolean
  hasBranchesError: boolean
  /** Shows "Edit" when set. */
  onEditConnection?: () => void
}

/**
 * One-line repository status: provider mark, repository name, whether the
 * repository is reachable, and an edit affordance.
 */
export function GitSyncRepoStatus({
  provider,
  repoName,
  branchesIsLoading,
  hasBranchesError,
  onEditConnection,
}: GitSyncRepoStatusProps) {
  return (
    <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
      <VcsProviderIcon provider={provider} size="sm" />
      <span className="min-w-0 truncate font-mono text-foreground/80">
        {repoName}
      </span>
      <ConnectionState
        isLoading={branchesIsLoading}
        hasError={hasBranchesError}
      />
      {onEditConnection && (
        <Button
          type="button"
          variant="link"
          size="sm"
          aria-label="Edit connection"
          className="h-auto shrink-0 p-0 text-xs font-normal text-foreground/70 underline underline-offset-2 hover:text-foreground"
          onClick={onEditConnection}
        >
          Edit
        </Button>
      )}
    </div>
  )
}

function ConnectionState({
  isLoading,
  hasError,
}: {
  isLoading: boolean
  hasError: boolean
}) {
  if (isLoading) {
    return <span className="shrink-0">Checking...</span>
  }
  if (hasError) {
    return (
      <span
        className="flex min-w-0 items-center gap-1 text-destructive"
        title="Could not reach repository"
      >
        <AlertTriangleIcon className="size-3.5 shrink-0" />
        <span className="truncate">Could not reach repository</span>
      </span>
    )
  }
  return (
    <span className="flex shrink-0 items-center gap-1.5">
      <span className="size-1.5 rounded-full bg-green-500" />
      Connected
    </span>
  )
}
