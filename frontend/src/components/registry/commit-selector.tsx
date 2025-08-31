"use client"

import { ChevronDownIcon, TagIcon } from "lucide-react"
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
import { cn } from "@/lib/utils"

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

  // If no commit is selected, default to HEAD (first commit)
  const effectiveCurrentSha = currentCommitSha || commits?.[0]?.sha
  const currentCommit = commits?.find(
    (commit) => commit.sha === effectiveCurrentSha
  )
  const isCurrentHead = commits?.[0]?.sha === effectiveCurrentSha

  if (isLoading) {
    return (
      <div className="flex items-center space-x-2">
        <Skeleton className="h-8 w-32" />
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
    sha: effectiveCurrentSha || "unknown",
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
            {effectiveCurrentSha && !isCurrentHead && (
              <Badge variant="outline" className="text-xs">
                Custom
              </Badge>
            )}
            {currentCommit?.tags?.map((tag) => (
              <Badge key={tag} variant="outline" className="text-xs">
                {tag}
              </Badge>
            ))}
          </div>
          <ChevronDownIcon className="size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start" className="w-96">
        <DropdownMenuLabel>Select commit</DropdownMenuLabel>
        <DropdownMenuSeparator />
        <div className="max-h-64 overflow-y-auto">
          {commits?.map((commit, index) => {
            const isSelected = commit.sha === effectiveCurrentSha
            const isHead = index === 0
            const commitDate = new Date(commit.date)
            const relativeTime = getRelativeTime(commitDate)

            return (
              <DropdownMenuItem
                key={commit.sha}
                onSelect={() => {
                  onSelectCommit(commit.sha)
                  setIsOpen(false)
                }}
                className={cn(
                  "flex flex-col items-start space-y-1 p-3",
                  isSelected && "bg-accent"
                )}
              >
                <div className="flex w-full flex-col space-y-1">
                  <div className="flex items-center justify-between">
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
                  {commit.tags && commit.tags.length > 0 && (
                    <div className="flex items-center space-x-1">
                      {commit.tags.map((tag) => (
                        <Badge
                          key={tag}
                          variant="outline"
                          className="text-xs flex items-center gap-1"
                        >
                          <TagIcon className="size-2.5" />
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  )}
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
