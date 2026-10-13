"use client"

import { useCallback, useEffect, useRef, useState } from "react"
import type { GitBranchInfo } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Input } from "@/components/ui/input"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { Skeleton } from "@/components/ui/skeleton"
import { buildRandomSyncBranchName } from "@/components/workspace-sync/push-target-policy"
import { GIT_SSH_URL_REGEX } from "@/lib/git"

const CREATE_NEW_BRANCH_VALUE = "__create_new_branch__"

/**
 * Resolves the repository default branch name, preferring the flagged default
 * branch and falling back to the first available branch.
 */
export function getWorkspaceSyncDefaultBranch(
  branches: GitBranchInfo[] | undefined
): string | undefined {
  return (
    branches?.find((candidate) => candidate.is_default)?.name ??
    branches?.[0]?.name
  )
}

/**
 * Returns the configured workspace sync base branch, if the Git URL pins one.
 */
export function getWorkspaceSyncConfiguredRef(
  gitUrl: string | null | undefined
): string | undefined {
  const trimmed = gitUrl?.trim()
  if (!trimmed) {
    return undefined
  }
  const ref = GIT_SSH_URL_REGEX.exec(trimmed)?.groups?.ref?.trim()
  return ref || undefined
}

/**
 * Returns the Git URL with its `@ref` set to `ref`, or removed when `ref` is
 * undefined so the repository default branch applies.
 */
export function withWorkspaceSyncConfiguredRef(
  gitUrl: string,
  ref: string | undefined
): string {
  const trimmed = gitUrl.trim()
  const currentRef = GIT_SSH_URL_REGEX.exec(trimmed)?.groups?.ref
  const base = currentRef
    ? trimmed.slice(0, trimmed.length - currentRef.length - 1)
    : trimmed
  return ref ? `${base}@${ref}` : base
}

/**
 * Resolves the branch that workspace sync exports use as their base.
 */
export function getWorkspaceSyncBaseBranch(
  gitUrl: string | null | undefined,
  branches: GitBranchInfo[] | undefined
): string | undefined {
  return (
    getWorkspaceSyncConfiguredRef(gitUrl) ??
    getWorkspaceSyncDefaultBranch(branches)
  )
}

function lastPushBranchStorageKey(scope: string): string {
  return `tracecat:git-sync:last-push-branch:${scope}`
}

function readLastPushBranch(scope: string): string | null {
  try {
    return window.localStorage.getItem(lastPushBranchStorageKey(scope))
  } catch {
    return null
  }
}

/** Remembers, in this browser, the branch last pushed to within a scope. */
export function writeLastPushBranch(scope: string, branch: string): void {
  try {
    window.localStorage.setItem(lastPushBranchStorageKey(scope), branch)
  } catch {
    // Blocked storage only loses the convenience.
  }
}

interface UseWorkspaceSyncBranchTargetOptions {
  branches: GitBranchInfo[] | undefined
  newBranchPrefix: string
  /** Starts on this scope's last pushed branch while it still exists. */
  rememberScope?: string
}

/**
 * Manages the repository branch target used by workspace sync push forms.
 */
export function useWorkspaceSyncBranchTarget({
  branches,
  newBranchPrefix,
  rememberScope,
}: UseWorkspaceSyncBranchTargetOptions) {
  const [branch, setBranch] = useState(() =>
    buildRandomSyncBranchName(newBranchPrefix)
  )
  const [isCreatingBranch, setIsCreatingBranch] = useState(true)
  // Set once restored or once the user picks, so a choice is never replaced.
  const settledRef = useRef(false)
  const hasBranches = (branches?.length ?? 0) > 0
  const defaultBranch = getWorkspaceSyncDefaultBranch(branches)

  useEffect(() => {
    if (settledRef.current || !rememberScope || !branches) {
      return
    }
    settledRef.current = true
    const last = readLastPushBranch(rememberScope)
    if (last && branches.some((candidate) => candidate.name === last)) {
      setIsCreatingBranch(false)
      setBranch(last)
    }
  }, [branches, rememberScope])

  const selectBranch = useCallback(
    (value: string) => {
      settledRef.current = true
      if (value === CREATE_NEW_BRANCH_VALUE) {
        setIsCreatingBranch(true)
        setBranch(buildRandomSyncBranchName(newBranchPrefix))
        return
      }
      setIsCreatingBranch(false)
      setBranch(value)
    },
    [newBranchPrefix]
  )

  const createBranch = useCallback((name: string) => {
    settledRef.current = true
    setIsCreatingBranch(true)
    setBranch(name)
  }, [])

  const resetBranchCreation = useCallback(() => {
    setIsCreatingBranch(true)
    setBranch(buildRandomSyncBranchName(newBranchPrefix))
  }, [newBranchPrefix])

  return {
    branch,
    setBranch,
    isCreatingBranch,
    selectBranch,
    createBranch,
    resetBranchCreation,
    defaultBranch,
    hasBranches,
  }
}

interface WorkspaceSyncBranchSelectorProps {
  id: string
  branches: GitBranchInfo[] | undefined
  branch: string
  isCreatingBranch: boolean
  branchesIsLoading: boolean
  hasBranches: boolean
  branchesError: unknown
  newBranchPlaceholder: string
  onSelectBranch: (value: string) => void
  onBranchChange: (value: string) => void
  showNoBranchesMessage?: boolean
}

/**
 * Shared target branch selector for workspace sync push forms.
 */
export function WorkspaceSyncBranchSelector({
  id,
  branches,
  branch,
  isCreatingBranch,
  branchesIsLoading,
  hasBranches,
  branchesError,
  newBranchPlaceholder,
  onSelectBranch,
  onBranchChange,
  showNoBranchesMessage = true,
}: WorkspaceSyncBranchSelectorProps) {
  return (
    <>
      <Select
        value={
          isCreatingBranch ||
          !branches?.some((candidate) => candidate.name === branch)
            ? CREATE_NEW_BRANCH_VALUE
            : branch
        }
        onValueChange={onSelectBranch}
        disabled={branchesIsLoading || !hasBranches}
      >
        <SelectTrigger id={id} className="min-w-0">
          {branchesIsLoading ? (
            <Skeleton className="h-4 w-full rounded-sm" />
          ) : (
            <SelectValue placeholder="Select branch" />
          )}
        </SelectTrigger>
        <SelectContent>
          {hasBranches ? (
            <>
              <SelectItem value={CREATE_NEW_BRANCH_VALUE}>
                Create new branch...
              </SelectItem>
              <SelectSeparator />
              {(branches ?? []).map((candidate) => (
                <SelectItem key={candidate.name} value={candidate.name}>
                  <div className="flex items-center gap-2">
                    <span>{candidate.name}</span>
                    {candidate.is_default && (
                      <Badge
                        variant="secondary"
                        className="h-4 rounded-sm px-1 text-[10px] font-normal"
                      >
                        default
                      </Badge>
                    )}
                  </div>
                </SelectItem>
              ))}
            </>
          ) : (
            <SelectItem value="__no_branches" disabled>
              No branches found
            </SelectItem>
          )}
        </SelectContent>
      </Select>
      {isCreatingBranch && (
        <div className="space-y-1">
          <Input
            value={branch}
            onChange={(event) => onBranchChange(event.target.value)}
            onBlur={(event) => onBranchChange(event.target.value.trim())}
            placeholder={newBranchPlaceholder}
          />
          <p className="text-[11px] text-muted-foreground">
            The branch will be created from the repository default branch.
          </p>
        </div>
      )}
      {!branchesIsLoading && !hasBranches && showNoBranchesMessage && (
        <p className="text-[11px] text-muted-foreground">
          No branches available from the configured repository.
        </p>
      )}
      {branchesError && (
        <p className="text-[11px] text-destructive">
          Failed to load repository branches.
        </p>
      )}
    </>
  )
}
