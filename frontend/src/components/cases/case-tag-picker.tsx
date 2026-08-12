"use client"

import { Plus } from "lucide-react"
import { useState } from "react"
import type { CaseTagRead } from "@/client"
import { Button } from "@/components/ui/button"
import { CheckIndicator } from "@/components/ui/check-indicator"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { useToast } from "@/components/ui/use-toast"
import { useAddCaseTag, useCaseTagCatalog, useRemoveCaseTag } from "@/lib/hooks"

/** Applied tags first, each group sorted alphabetically. */
function sortAppliedFirst(
  catalogTags: CaseTagRead[],
  appliedTags: CaseTagRead[] | undefined
): CaseTagRead[] {
  const appliedTagIds = new Set(appliedTags?.map((tag) => tag.id) ?? [])
  return [...catalogTags].sort((a, b) => {
    const aApplied = appliedTagIds.has(a.id)
    const bApplied = appliedTagIds.has(b.id)
    if (aApplied !== bApplied) {
      return aApplied ? -1 : 1
    }
    return a.name.localeCompare(b.name)
  })
}

interface CaseTagPickerListProps {
  catalogTags: CaseTagRead[]
  appliedTags: CaseTagRead[] | undefined
  onToggle: (tagId: string, hasTag: boolean) => Promise<void>
}

function CaseTagPickerList({
  catalogTags,
  appliedTags,
  onToggle,
}: CaseTagPickerListProps) {
  // Snapshot the row order for this open session. `PopoverContent` unmounts on
  // close, so the initializer reruns on the next open. Sorting against a live
  // selection would reshuffle rows under the pointer on every toggle.
  const [orderedTags] = useState(() =>
    sortAppliedFirst(catalogTags, appliedTags)
  )

  return (
    <Command>
      <CommandInput placeholder="Search tags..." className="text-xs" />
      <CommandList>
        <CommandEmpty>No tags found.</CommandEmpty>
        <CommandGroup>
          {orderedTags.map((tag) => {
            const hasTag = appliedTags?.some((t) => t.id === tag.id)
            return (
              <CommandItem
                key={tag.id}
                value={tag.name}
                className="group text-xs"
                onSelect={async () => {
                  await onToggle(tag.id, !!hasTag)
                }}
              >
                <CheckIndicator checked={!!hasTag} />
                <div
                  className="size-2 shrink-0 rounded-full"
                  style={{
                    backgroundColor: tag.color || undefined,
                  }}
                />
                <span>{tag.name}</span>
              </CommandItem>
            )
          })}
        </CommandGroup>
      </CommandList>
    </Command>
  )
}

interface CaseTagPickerProps {
  caseId: string
  workspaceId: string
  /** Tags currently applied to the case. */
  appliedTags: CaseTagRead[] | undefined
}

/**
 * Popover for applying and removing workspace tags on a case.
 *
 * Renders nothing when the workspace tag catalog is empty.
 */
export function CaseTagPicker({
  caseId,
  workspaceId,
  appliedTags,
}: CaseTagPickerProps) {
  const { caseTags } = useCaseTagCatalog(workspaceId)
  const { addCaseTag } = useAddCaseTag({ caseId, workspaceId })
  const { removeCaseTag } = useRemoveCaseTag({ caseId, workspaceId })
  const { toast } = useToast()

  const handleTagToggle = async (tagId: string, hasTag: boolean) => {
    try {
      if (hasTag) {
        // Remove tag
        await removeCaseTag(tagId)
      } else {
        // Add tag
        await addCaseTag({ tag_id: tagId })
      }
    } catch (error) {
      console.error("Failed to modify tag:", error)
      toast({
        title: "Error",
        description: `Failed to ${hasTag ? "remove" : "add"} tag ${hasTag ? "from" : "to"} case. Please try again.`,
        variant: "destructive",
      })
    }
  }

  if (!caseTags || caseTags.length === 0) {
    return null
  }

  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button variant="ghost" size="sm" className="size-6 shrink-0 p-0">
          <Plus className="h-4 w-4" />
          <span className="sr-only">Manage tags</span>
        </Button>
      </PopoverTrigger>
      <PopoverContent
        align="end"
        className="w-56 p-0"
        onClick={(e) => e.stopPropagation()}
      >
        <CaseTagPickerList
          catalogTags={caseTags}
          appliedTags={appliedTags}
          onToggle={handleTagToggle}
        />
      </PopoverContent>
    </Popover>
  )
}
