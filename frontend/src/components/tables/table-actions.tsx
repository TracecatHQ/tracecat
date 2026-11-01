"use client"

import { Copy, FolderKanban, Trash2 } from "lucide-react"
import type { TableReadMinimal } from "@/client"
import {
  DropdownMenuGroup,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"

export function TableActions({
  table,
  onDeleteClick,
  onMoveClick,
}: {
  table: TableReadMinimal
  onDeleteClick: (table: TableReadMinimal) => void
  onMoveClick?: (table: TableReadMinimal) => void
}) {
  return (
    <DropdownMenuGroup>
      <DropdownMenuItem
        className="text-xs"
        onClick={(e) => {
          e.stopPropagation() // Prevent row click
          navigator.clipboard.writeText(table.id)
          toast({
            title: "Table ID copied",
            description: table.id,
          })
        }}
      >
        <Copy className="mr-2 h-3 w-3" />
        Copy table ID
      </DropdownMenuItem>
      {onMoveClick ? (
        <DropdownMenuItem
          className="text-xs"
          onClick={(e) => {
            e.stopPropagation()
            onMoveClick(table)
          }}
        >
          <FolderKanban className="mr-2 h-3 w-3" />
          Move to folder
        </DropdownMenuItem>
      ) : null}
      <DropdownMenuItem
        className="text-xs text-destructive focus:text-destructive"
        onClick={(e) => {
          e.stopPropagation() // Prevent row click
          onDeleteClick(table)
        }}
      >
        <Trash2 className="mr-2 h-3 w-3" />
        Delete
      </DropdownMenuItem>
    </DropdownMenuGroup>
  )
}
