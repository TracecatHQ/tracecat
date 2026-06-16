"use client"

import { useCallback, useEffect, useState } from "react"
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

const CREATE_NEW_BRANCH_VALUE = "__create_new_branch__"

interface UseWorkspaceSyncBranchTargetOptions {
  branches: GitBranchInfo[] | undefined
  enabled: boolean
  newBranchName: string
}

/**
 * Manages the repository branch target used by workspace sync push forms.
 */
export function useWorkspaceSyncBranchTarget({
  branches,
  enabled,
  newBranchName,
}: UseWorkspaceSyncBranchTargetOptions) {
  const [branch, setBranch] = useState("")
  const [isCreatingBranch, setIsCreatingBranch] = useState(false)
  const hasBranches = (branches?.length ?? 0) > 0
  const defaultBranch =
    branches?.find((candidate) => candidate.is_default)?.name ??
    branches?.[0]?.name

  useEffect(() => {
    if (!enabled || !branches || branches.length === 0 || isCreatingBranch) {
      return
    }

    const branchNames = new Set(branches.map((candidate) => candidate.name))
    if (branch && branchNames.has(branch)) {
      return
    }

    if (defaultBranch) {
      setBranch(defaultBranch)
    }
  }, [branch, branches, defaultBranch, enabled, isCreatingBranch])

  const selectBranch = useCallback(
    (value: string) => {
      if (value === CREATE_NEW_BRANCH_VALUE) {
        setIsCreatingBranch(true)
        setBranch(newBranchName)
        return
      }
      setIsCreatingBranch(false)
      setBranch(value)
    },
    [newBranchName]
  )

  const resetBranchCreation = useCallback(() => {
    setIsCreatingBranch(false)
  }, [])

  return {
    branch,
    setBranch,
    isCreatingBranch,
    selectBranch,
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
