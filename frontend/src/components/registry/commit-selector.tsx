"use client"

import { ChevronDownIcon, LoaderCircleIcon } from "lucide-react"
import { useState } from "react"
import type { GitCommitInfo } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Skeleton } from "@/components/ui/skeleton"
import { getRelativeTime } from "@/lib/event-history"

interface CommitSelectorProps {
  commits: GitCommitInfo[] | undefined
  currentCommitSha: string | null
  isLoading: boolean
  error: Error | null
  onSelectCommit: (commitSha: string) => void
  disabled?: boolean
}

export function CommitSelector({
  commits,
  currentCommitSha,
  isLoading,
  error,
  onSelectCommit,
  disabled = false,
}: CommitSelectorProps) {
  const [isOpen, setIsOpen] = useState(false)

  const currentCommit = commits?.find(
    (commit) => commit.sha === currentCommitSha
  )
  const isCurrentHead = commits?.[0]?.sha === currentCommitSha

  if (isLoading) {
    return (
      <div className="flex items-center space-x-2">
        <Skeleton className="h-6 w-20" />
        <LoaderCircleIcon className="size-4 animate-spin" />
      </div>
    )
  }

  if (error) {
    return (
      <div className="text-sm text-muted-foreground">
        Failed to load commits
      </div>
    )
  }

  const displayCommit = currentCommit || {
    sha: currentCommitSha || "unknown",
    message: "Unknown commit",
    author: "",
    author_email: "",
    date: "",
  }

  return (
    <DropdownMenu open={isOpen} onOpenChange={setIsOpen}>
      <DropdownMenuTrigger asChild>
        <Button
          variant="outline"
          size="sm"
          disabled={disabled || !commits?.length}
          className="justify-between font-mono text-xs"
        >
          <div className="flex items-center space-x-2">
            <span>{displayCommit.sha.substring(0, 7)}</span>
            {isCurrentHead && (
              <Badge variant="secondary" className="text-xs">
                HEAD
              </Badge>
            )}
            {currentCommitSha && !isCurrentHead && (
              <Badge variant="outline" className="text-xs">
                Custom
              </Badge>
            )}
          </div>
          <ChevronDownIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-96">
        <DropdownMenuLabel>Select commit</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <div className="max-h-64 overflow-y-auto">
          {commits?.map((commit, index) => {
            const isSelected = commit.sha === currentCommitSha
            const isHead = index === 0
            const commitDate = new Date(commit.date)
            const relativeTime = getRelativeTime(commitDate)

            return (
              <DropdownMenuItem
                key={commit.sha}
                onClick={() => {
                  onSelectCommit(commit.sha)
                  setIsOpen(false)
                }}
                className={`flex flex-col items-start space-y-1 p-3 ${
                  isSelected ? "bg-accent" : ""
                }`}
              >
                <div className="flex w-full items-center justify-between">
                  <div className="flex items-center space-x-2">
                    <span className="font-mono text-sm font-medium">
                      {commit.sha.substring(0, 7)}
                    </span>
                    {isHead && (
                      <Badge variant="secondary" className="text-xs">
                        HEAD
                      </Badge>
                    )}
                    {isSelected && !isHead && (
                      <Badge variant="default" className="text-xs">
                        Current
                      </Badge>
                    )}
                    {isSelected && isHead && (
                      <Badge variant="default" className="text-xs">
                        Current • HEAD
                      </Badge>
                    )}
                  </div>
                  <span className="text-xs text-muted-foreground">
                    {relativeTime}
                  </span>
                </div>
                <div className="w-full text-left">
                  <p className="line-clamp-1 text-sm text-foreground">
                    {commit.message}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    by {commit.author} • {commitDate.toLocaleDateString()}
                  </p>
                </div>
              </DropdownMenuItem>
            )
          })}
        </div>
        {!commits?.length && (
          <div className="p-3 text-center text-sm text-muted-foreground">
            No commits found
          </div>
        )}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
