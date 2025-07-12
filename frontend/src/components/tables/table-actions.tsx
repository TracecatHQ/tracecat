"use client"

import { Copy, Trash2 } from "lucide-react"
import type { TableReadMinimal } from "@/client"
import {
  DropdownMenuGroup,
  DropdownMenuItem,
} from "@/components/ui/dropdown-menu"
import { toast } from "@/components/ui/use-toast"

export function TableActions({
  table,
  onDeleteClick,
}: {
  table: TableReadMinimal
  onDeleteClick: (table: TableReadMinimal) => void
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
