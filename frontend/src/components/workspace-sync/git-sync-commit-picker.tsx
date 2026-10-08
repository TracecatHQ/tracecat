"use client"

import { BotIcon, CheckIcon, ChevronDownIcon, TagIcon } from "lucide-react"
import { useState } from "react"
import type { GitCommitInfo } from "@/client"
import { Badge } from "@/components/ui/badge"
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Skeleton } from "@/components/ui/skeleton"
import { getRelativeTime } from "@/lib/event-history"
import { cn } from "@/lib/utils"

interface GitSyncCommitPickerProps {
  commits: GitCommitInfo[] | undefined
  commitSha: string | undefined
  isLoading: boolean
  hasError: boolean
  disabled?: boolean
  /** Opens the popover upward when the field sits in the footer. */
  side?: "top" | "bottom"
  className?: string
  onSelectCommit: (sha: string) => void
}

/**
 * Select-style commit field for the pull source. The popover searches commits
 * by message, author, or SHA and shows who made each one.
 */
export function GitSyncCommitPicker({
  commits,
  commitSha,
  isLoading,
  hasError,
  disabled = false,
  side = "bottom",
  className,
  onSelectCommit,
}: GitSyncCommitPickerProps) {
  const [open, setOpen] = useState(false)

  if (isLoading) {
    return <Skeleton className={cn("h-8 w-full rounded-md", className)} />
  }
  if (hasError) {
    return (
      <p className="flex h-8 items-center text-xs text-destructive">
        Could not load commits.
      </p>
    )
  }
  if (!commits?.length) {
    return (
      <p className="flex h-8 items-center text-xs text-muted-foreground">
        No commits yet.
      </p>
    )
  }

  const selectedIndex = commits.findIndex((commit) => commit.sha === commitSha)
  const selected = commits[selectedIndex]

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <button
          type="button"
          role="combobox"
          aria-label="Commit"
          aria-expanded={open}
          disabled={disabled}
          className={cn(
            "flex h-8 w-full min-w-0 items-center gap-2 rounded-md border bg-background px-2.5 text-left text-xs text-foreground hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
            className
          )}
        >
          {selected ? (
            <>
              <span className="shrink-0 font-mono text-xs font-medium">
                {selected.sha.substring(0, 7)}
              </span>
              <span className="min-w-0 truncate">
                {getCommitTitle(selected)}
              </span>
              {selected.date && (
                <span className="shrink-0 text-xs text-muted-foreground">
                  · {getRelativeTime(new Date(selected.date))}
                </span>
              )}
              {selectedIndex === 0 && <LatestTag />}
            </>
          ) : (
            <span className="text-muted-foreground">Pick a commit</span>
          )}
          <ChevronDownIcon className="ml-auto size-3.5 shrink-0 text-muted-foreground" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        align="start"
        side={side}
        className="w-[var(--radix-popover-trigger-width)] min-w-80 p-0"
      >
        <Command>
          <CommandInput placeholder="Search by message, author, or SHA" />
          <CommandList className="max-h-72">
            <CommandEmpty className="py-4 text-center text-xs text-muted-foreground">
              No matching commits
            </CommandEmpty>
            {commits.map((commit, index) => (
              <CommandItem
                key={commit.sha}
                value={commit.sha}
                keywords={[
                  commit.sha.substring(0, 7),
                  commit.message,
                  commit.author,
                  ...(commit.tags ?? []),
                ]}
                onSelect={() => {
                  onSelectCommit(commit.sha)
                  setOpen(false)
                }}
                className="items-start gap-2.5 px-2.5 py-2"
              >
                <AuthorAvatar author={commit.author} />
                <CommitRow commit={commit} isLatest={index === 0} />
                <CheckIcon
                  className={cn(
                    "mt-0.5 size-3.5 shrink-0",
                    commit.sha === commitSha ? "opacity-100" : "opacity-0"
                  )}
                />
              </CommandItem>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}

function CommitRow({
  commit,
  isLatest,
}: {
  commit: GitCommitInfo
  isLatest: boolean
}) {
  return (
    <span className="flex min-w-0 flex-1 flex-col gap-0.5">
      <span className="flex min-w-0 items-center gap-2">
        <span className="min-w-0 truncate text-[13px] font-medium">
          {getCommitTitle(commit)}
        </span>
        {isLatest && <LatestTag />}
      </span>
      <span className="flex min-w-0 flex-wrap items-center gap-x-1.5 gap-y-1 text-xs text-muted-foreground">
        <span className="font-mono">{commit.sha.substring(0, 7)}</span>
        <span>·</span>
        <span className="truncate">{commit.author || "Unknown author"}</span>
        {commit.date && (
          <>
            <span>·</span>
            <span title={new Date(commit.date).toLocaleString()}>
              {getRelativeTime(new Date(commit.date))}
            </span>
          </>
        )}
        {commit.tags?.map((tag) => (
          <span key={tag} className="flex items-center gap-0.5">
            <TagIcon className="size-3" />
            {tag}
          </span>
        ))}
      </span>
    </span>
  )
}

function AuthorAvatar({ author }: { author: string }) {
  return (
    <span
      aria-hidden
      className="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full bg-muted text-[10px] font-medium text-muted-foreground"
    >
      {isBotAuthor(author) ? (
        <BotIcon className="size-3.5" />
      ) : (
        getAuthorInitials(author)
      )}
    </span>
  )
}

function LatestTag() {
  return (
    <Badge
      variant="secondary"
      className="h-4 shrink-0 rounded px-1.5 text-[11px] font-medium"
    >
      latest
    </Badge>
  )
}

function getCommitTitle(commit: GitCommitInfo): string {
  return commit.message.split("\n")[0]
}

/** Service accounts commit as "name[bot]". */
function isBotAuthor(author: string): boolean {
  return author.toLowerCase().includes("[bot]")
}

function getAuthorInitials(author: string): string {
  const parts = author
    .replace(/\[bot\]/gi, "")
    .replace(/[-_]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean)
  if (parts.length === 0) {
    return "?"
  }
  if (parts.length === 1) {
    return parts[0].slice(0, 2).toUpperCase()
  }
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}
