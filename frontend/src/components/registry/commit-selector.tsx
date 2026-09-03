"use client"

import { Bot, ChevronDownIcon, TagIcon } from "lucide-react"
import { useState } from "react"
import type { GitCommitInfo } from "@/client"
import { Avatar, AvatarFallback } from "@/components/ui/avatar"
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

/** Returns true when a commit author is a service/bot account, e.g. "name[bot]". */
function isBotAuthor(author: string): boolean {
  return author.toLowerCase().includes("[bot]")
}

/** Derives up to two uppercase initials from a commit author name. */
function getAuthorInitials(author: string): string {
  const cleaned = author
    .replace(/\[bot\]/gi, "")
    .replace(/[-_]+/g, " ")
    .trim()
  const parts = cleaned.split(/\s+/).filter(Boolean)
  if (parts.length === 0) {
    return "?"
  }
  if (parts.length === 1) {
    return parts[0].slice(0, 2).toUpperCase()
  }
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
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
        <div className="max-h-64 overflow-y-auto p-1">
          {commits?.map((commit, index) => {
            const isSelected = commit.sha === effectiveCurrentSha
            const isHead = index === 0
            const isBot = isBotAuthor(commit.author)
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
                  "relative flex cursor-pointer items-start gap-2.5 p-2.5",
                  isSelected && "bg-primary/5"
                )}
              >
                {isSelected && (
                  <span
                    aria-hidden
                    className="absolute inset-y-1.5 left-0 w-0.5 rounded-full bg-primary"
                  />
                )}
                <Avatar className="mt-0.5 size-7 flex-none">
                  <AvatarFallback className="bg-muted text-[10px] font-medium text-muted-foreground">
                    {isBot ? (
                      <Bot className="size-3.5" />
                    ) : (
                      getAuthorInitials(commit.author)
                    )}
                  </AvatarFallback>
                </Avatar>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="font-mono text-xs font-semibold">
                      {commit.sha.substring(0, 7)}
                    </span>
                    {isSelected && (
                      <Badge
                        variant="outline"
                        className="border-transparent bg-primary/10 text-xs font-medium text-primary"
                      >
                        Current
                      </Badge>
                    )}
                    {isHead && (
                      <Badge variant="secondary" className="text-xs">
                        HEAD
                      </Badge>
                    )}
                    <span
                      className="ml-auto whitespace-nowrap text-xs text-muted-foreground"
                      title={commitDate.toLocaleString()}
                    >
                      {relativeTime}
                    </span>
                  </div>
                  <p className="mt-1 line-clamp-1 text-[13px] text-foreground">
                    {commit.message}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {commit.author || "Unknown"} •{" "}
                    {commitDate.toLocaleDateString()}
                  </p>
                  {commit.tags && commit.tags.length > 0 && (
                    <div className="mt-1.5 flex flex-wrap items-center gap-1">
                      {commit.tags.map((tag) => (
                        <Badge
                          key={tag}
                          variant="outline"
                          className="flex items-center gap-1 text-xs font-normal text-muted-foreground"
                        >
                          <TagIcon className="size-2.5" />
                          {tag}
                        </Badge>
                      ))}
                    </div>
                  )}
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
