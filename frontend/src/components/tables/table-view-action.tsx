"use client"

import { DotsHorizontalIcon } from "@radix-ui/react-icons"
import type { Row } from "@tanstack/react-table"
import { CopyIcon, Trash2Icon } from "lucide-react"
import { useState } from "react"
import type { TableRowRead } from "@/client"
import { TableViewActionDeleteDialog } from "@/components/tables/table-view-action-delete-dialog"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useAuth } from "@/hooks/use-auth"

type TableViewActionType = "delete" | "insert" | null

export function TableViewAction({ row }: { row: Row<TableRowRead> }) {
  const { user } = useAuth()
  const [activeType, setActiveType] = useState<TableViewActionType>(null)
  const onOpenChange = () => setActiveType(null)

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="ghost" className="size-4 p-0">
            <span className="sr-only">Open menu</span>
            <DotsHorizontalIcon className="size-4" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem
            className="py-1 text-xs text-foreground/80"
            onClick={(e) => {
              e.stopPropagation()
              navigator.clipboard.writeText(String(row.original.id))
            }}
          >
            <CopyIcon className="mr-2 size-3 group-hover/item:text-accent-foreground" />
            Copy ID
          </DropdownMenuItem>
          {user?.isPrivileged() && (
            <>
              <DropdownMenuSeparator />
              <DropdownMenuItem
                className="py-1 text-xs text-destructive"
                onClick={(e) => {
                  e.stopPropagation()
                  setActiveType("delete")
                }}
              >
                <Trash2Icon className="mr-2 size-3 group-hover/item:text-destructive" />
                Delete
              </DropdownMenuItem>
            </>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
      <TableViewActionDeleteDialog
        row={row}
        open={activeType === "delete"}
        onOpenChange={onOpenChange}
      />
    </>
  )
}
