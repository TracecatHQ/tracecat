"use client"

import { ChevronDownIcon, GitBranchIcon, PlusIcon } from "lucide-react"
import { type FormEvent, useState } from "react"
import type { GitBranchInfo } from "@/client"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Skeleton } from "@/components/ui/skeleton"
import { buildRandomSyncBranchName } from "@/components/workspace-sync/push-target-policy"
import { cn } from "@/lib/utils"

const NEW_BRANCH_PREFIX = "sync/workspace"

interface GitSyncBranchPickerProps {
  branches: GitBranchInfo[] | undefined
  branch: string
  isCreatingBranch: boolean
  baseBranch: string | undefined
  isLoading: boolean
  hasError: boolean
  disabled?: boolean
  /** Footer placement opens the popover upward and drops the subline. */
  placement?: "inline" | "footer"
  className?: string
  onCreateBranch: (name: string) => void
  onSelectBranch: (name: string) => void
}

/**
 * Select-style branch field for the push destination. The popover creates a
 * new branch off the base branch or picks an existing one.
 */
export function GitSyncBranchPicker({
  branches,
  branch,
  isCreatingBranch,
  baseBranch,
  isLoading,
  hasError,
  disabled = false,
  placement = "inline",
  className,
  onCreateBranch,
  onSelectBranch,
}: GitSyncBranchPickerProps) {
  const isFooter = placement === "footer"
  const [open, setOpen] = useState(false)
  const [newName, setNewName] = useState(branch)
  const isDefault = !isCreatingBranch && branch === baseBranch
  const trimmedNewName = newName.trim()
  const canCreate = trimmedNewName !== "" && !/\s/.test(trimmedNewName)

  function handleOpenChange(next: boolean) {
    if (next) {
      setNewName(
        isCreatingBranch ? branch : buildRandomSyncBranchName(NEW_BRANCH_PREFIX)
      )
    }
    setOpen(next)
  }

  function handleCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canCreate) {
      return
    }
    if (branches?.some((candidate) => candidate.name === trimmedNewName)) {
      onSelectBranch(trimmedNewName)
    } else {
      onCreateBranch(trimmedNewName)
    }
    setOpen(false)
  }

  if (isLoading) {
    return <Skeleton className={cn("h-8 w-full rounded-md", className)} />
  }

  return (
    <div className={cn("flex flex-col gap-1.5", className)}>
      <Popover open={open} onOpenChange={handleOpenChange}>
        <PopoverTrigger asChild>
          <button
            type="button"
            aria-label="Branch"
            disabled={disabled}
            className="flex h-8 w-full min-w-0 items-center gap-2 rounded-md border bg-background px-2.5 text-left hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-inset focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50"
          >
            <GitBranchIcon className="size-3.5 shrink-0 text-muted-foreground" />
            <span className="min-w-0 truncate font-mono text-xs font-medium text-foreground">
              {branch || "Pick a branch"}
            </span>
            {isCreatingBranch && <BranchTag>new</BranchTag>}
            {isDefault && <BranchTag>default</BranchTag>}
            <ChevronDownIcon className="ml-auto size-3.5 shrink-0 text-muted-foreground" />
          </button>
        </PopoverTrigger>
        <PopoverContent
          align="start"
          side={isFooter ? "top" : "bottom"}
          className="w-[var(--radix-popover-trigger-width)] min-w-72 p-2"
        >
          <form className="flex flex-col gap-1.5" onSubmit={handleCreate}>
            <label
              htmlFor="git-sync-new-branch"
              className="px-1 text-[11px] font-medium text-muted-foreground"
            >
              New branch from {baseBranch ?? "the default branch"}
            </label>
            <div className="flex gap-1.5">
              <Input
                id="git-sync-new-branch"
                value={newName}
                onChange={(event) => setNewName(event.target.value)}
                className="h-8 font-mono text-xs"
              />
              <Button
                type="submit"
                size="sm"
                className="h-8 shrink-0 gap-1"
                disabled={!canCreate}
              >
                <PlusIcon className="size-3.5" />
                Create
              </Button>
            </div>
          </form>
          <div className="-mx-2 my-2 h-px bg-border" />
          <span className="px-1 text-[11px] font-medium text-muted-foreground">
            Existing branches
          </span>
          {hasError ? (
            <p className="px-1 py-2 text-xs text-destructive">
              Could not load branches.
            </p>
          ) : (
            <Command className="mt-1">
              {(branches?.length ?? 0) > 8 && (
                <CommandInput placeholder="Search branches" className="h-8" />
              )}
              <CommandList className="max-h-56">
                <CommandEmpty className="py-3 text-center text-xs text-muted-foreground">
                  No branches found
                </CommandEmpty>
                {(branches ?? []).map((candidate) => (
                  <CommandItem
                    key={candidate.name}
                    value={candidate.name}
                    onSelect={() => {
                      onSelectBranch(candidate.name)
                      setOpen(false)
                    }}
                    className="gap-2"
                  >
                    <GitBranchIcon className="size-3.5 shrink-0 text-muted-foreground" />
                    <span className="truncate font-mono text-xs">
                      {candidate.name}
                    </span>
                    {candidate.is_default && (
                      <span className="text-[11px] text-muted-foreground">
                        default
                      </span>
                    )}
                  </CommandItem>
                ))}
              </CommandList>
            </Command>
          )}
        </PopoverContent>
      </Popover>
      {!isFooter && (
        <span className="text-xs text-muted-foreground">
          {getBranchSubline({ isCreatingBranch, isDefault, baseBranch })}
        </span>
      )}
    </div>
  )
}

function BranchTag({ children }: { children: string }) {
  return (
    <Badge
      variant="secondary"
      className="h-4 shrink-0 rounded px-1.5 text-[11px] font-medium"
    >
      {children}
    </Badge>
  )
}

function getBranchSubline({
  isCreatingBranch,
  isDefault,
  baseBranch,
}: {
  isCreatingBranch: boolean
  isDefault: boolean
  baseBranch: string | undefined
}): string {
  if (isCreatingBranch) {
    return `Branches off ${baseBranch ?? "the default branch"}`
  }
  if (isDefault) {
    return "Default branch"
  }
  return "Updates this branch"
}
