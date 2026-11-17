"use client"

import { useState } from "react"
import type { GitTagInfo, ManagedTargetRead } from "@/client"
import { Button } from "@/components/ui/button"
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select"
import { tagAge } from "../managed-package-utils"

/** Props for {@link BumpControls}. */
export interface BumpControlsProps {
  selectedTargets: ManagedTargetRead[]
  tags: GitTagInfo[]
  isPending: boolean
  onPreview: (tag: string) => void
  onClearSelection: () => void
}

/** Pick a tag for the selected targets and start a preview rollout. */
export function BumpControls({
  selectedTargets,
  tags,
  isPending,
  onPreview,
  onClearSelection,
}: BumpControlsProps) {
  const [tag, setTag] = useState<string | null>(tags[0]?.name ?? null)
  // Fall back to the latest tag when the picked one is no longer listed.
  const tagExists = tags.some((option) => option.name === tag)
  const chosenTag = tagExists ? tag : (tags[0]?.name ?? null)
  const chosenAge = tagAge(tags, chosenTag)

  const rollbackCount = selectedTargets.filter((target) => {
    const currentAge = tagAge(
      tags,
      target.last_applied_tag ?? target.pinned_tag
    )
    return currentAge >= 0 && chosenAge > currentAge
  }).length

  return (
    <div className="space-y-2 rounded-md border p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm">
          {selectedTargets.length} target
          {selectedTargets.length === 1 ? "" : "s"} selected
        </span>
        <div className="ml-auto flex items-center gap-2">
          <Select
            value={chosenTag ?? undefined}
            onValueChange={setTag}
            disabled={tags.length === 0}
          >
            <SelectTrigger className="h-8 w-40 text-xs">
              <SelectValue placeholder="Select a tag" />
            </SelectTrigger>
            <SelectContent>
              {tags.map((option, index) => (
                <SelectItem key={option.name} value={option.name}>
                  <span className="font-mono">{option.name}</span>
                  {index === 0 && (
                    <span className="ml-2 text-muted-foreground">latest</span>
                  )}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
          <Button
            size="sm"
            onClick={() => chosenTag && onPreview(chosenTag)}
            disabled={!chosenTag || isPending}
          >
            {isPending ? "Starting..." : "Preview"}
          </Button>
          <Button size="sm" variant="ghost" onClick={onClearSelection}>
            Clear
          </Button>
        </div>
      </div>
      {rollbackCount > 0 && (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          {chosenTag} is older than what {rollbackCount} of the selected target
          {rollbackCount === 1 ? " has" : "s have"} applied. Applying it rolls
          {rollbackCount === 1 ? " that target" : " those targets"} back.
        </p>
      )}
    </div>
  )
}
