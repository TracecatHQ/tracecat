"use client"

import { useQueryClient } from "@tanstack/react-query"
import { Copy, ExternalLink, TagsIcon, Trash2 } from "lucide-react"
import Link from "next/link"
import type { CaseReadMinimal } from "@/client"
import { casesAddTag, casesRemoveTag } from "@/client"
import { DeleteCaseAlertDialogTrigger } from "@/components/cases/delete-case-dialog"
import {
  DropdownMenuCheckboxItem,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuPortal,
  DropdownMenuSub,
  DropdownMenuSubContent,
  DropdownMenuSubTrigger,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"
import { useCaseTagCatalog } from "@/lib/hooks"
import { useWorkspaceId } from "@/providers/workspace-id"

export function CaseActions({
  item,
  setSelectedCase,
}: {
  item: CaseReadMinimal
  setSelectedCase: (case_: CaseReadMinimal) => void
}) {
  const workspaceId = useWorkspaceId()
  const { caseTags } = useCaseTagCatalog(workspaceId)
  const queryClient = useQueryClient()

  const handleTagToggle = async (tagId: string, hasTag: boolean) => {
    try {
      if (hasTag) {
        // Remove tag
        await casesRemoveTag({
          caseId: item.id,
          tagIdentifier: tagId,
          workspaceId,
        })
        const tag = caseTags?.find((t) => t.id === tagId)
        toast({
          title: "Tag removed",
          description: `Successfully removed tag "${tag?.name}" from case`,
        })
      } else {
        // Add tag
        await casesAddTag({
          caseId: item.id,
          workspaceId,
          requestBody: {
            tag_id: tagId,
          },
        })
        const tag = caseTags?.find((t) => t.id === tagId)
        toast({
          title: "Tag added",
          description: `Successfully added tag "${tag?.name}" to case`,
        })
      }

      // Invalidate queries to refresh the data
      await queryClient.invalidateQueries({ queryKey: ["cases"] })
    } catch (error) {
      console.error("Failed to modify tag:", error)
      toast({
        title: "Error",
        description: `Failed to ${hasTag ? "remove" : "add"} tag ${hasTag ? "from" : "to"} case`,
        variant: "destructive",
      })
    }
  }

  return (
    <DropdownMenuGroup>
      <DropdownMenuItem
        className="text-xs"
        onClick={(e) => e.stopPropagation()}
        asChild
      >
        <Link
          href={`/workspaces/${workspaceId}/cases/${item.id}`}
          target="_blank"
          rel="noopener noreferrer"
        >
          <ExternalLink className="mr-2 size-3.5" />
          Open in new tab
        </Link>
      </DropdownMenuItem>

      {caseTags && caseTags.length > 0 ? (
        <DropdownMenuSub>
          <DropdownMenuSubTrigger
            className="text-xs"
            onClick={(e) => e.stopPropagation()}
          >
            <TagsIcon className="mr-2 size-3.5" />
            Tags
          </DropdownMenuSubTrigger>
          <DropdownMenuPortal>
            <DropdownMenuSubContent>
              {caseTags.map((tag) => {
                const hasTag = item.tags?.some((t) => t.id === tag.id)
                return (
                  <DropdownMenuCheckboxItem
                    key={tag.id}
                    className="text-xs"
                    checked={hasTag}
                    onClick={async (e) => {
                      e.stopPropagation()
                      await handleTagToggle(tag.id, !!hasTag)
                    }}
                  >
                    <div
                      className="mr-2 flex size-2 rounded-full"
                      style={{
                        backgroundColor: tag.color || undefined,
                      }}
                    />
                    <span>{tag.name}</span>
                  </DropdownMenuCheckboxItem>
                )
              })}
            </DropdownMenuSubContent>
          </DropdownMenuPortal>
        </DropdownMenuSub>
      ) : (
        <DropdownMenuItem
          className="!bg-transparent text-xs !text-muted-foreground hover:cursor-not-allowed"
          onClick={(e) => e.stopPropagation()}
        >
          <TagsIcon className="mr-2 size-3.5" />
          <span>No tags available</span>
        </DropdownMenuItem>
      )}

      <DropdownMenuItem
        className="text-xs"
        onClick={async (e) => {
          e.stopPropagation()
          try {
            await navigator.clipboard.writeText(item.id)
            toast({
              title: "Case ID copied",
              description: (
                <div className="flex flex-col space-y-2">
                  <span>
                    Case ID copied for{" "}
                    <b className="inline-block">{item.short_id}</b>
                  </span>
                  <span className="text-muted-foreground">ID: {item.id}</span>
                </div>
              ),
            })
          } catch (error) {
            console.error("Failed to copy to clipboard:", error)
            toast({
              title: "Failed to copy",
              description: "Could not copy case ID to clipboard",
              variant: "destructive",
            })
          }
        }}
      >
        <Copy className="mr-2 size-3.5" />
        Copy case ID
      </DropdownMenuItem>

      {/* Danger zone */}
      <DeleteCaseAlertDialogTrigger asChild>
        <DropdownMenuItem
          className="text-xs text-rose-500 focus:text-rose-600"
          onClick={(e) => {
            e.stopPropagation()
            setSelectedCase(item)
          }}
        >
          <Trash2 className="mr-2 size-3.5" />
          Delete
        </DropdownMenuItem>
      </DeleteCaseAlertDialogTrigger>
    </DropdownMenuGroup>
  )
}
