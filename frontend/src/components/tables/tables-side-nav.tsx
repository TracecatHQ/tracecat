"use client"

import { useState } from "react"
import { useParams, useRouter } from "next/navigation"
import { TableReadMinimal } from "@/client"
import { useAuth } from "@/providers/auth"
import {
  CopyIcon,
  EllipsisIcon,
  PencilIcon,
  Plus,
  Table2Icon,
  Trash2Icon,
} from "lucide-react"

import { userIsPrivileged } from "@/lib/auth"
import { useListTables } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { CreateTableDialog } from "@/components/tables/table-create-dialog"
import { DeleteTableDialog } from "@/components/tables/table-delete-dialog"
import { TableEditDialog } from "@/components/tables/table-edit-dialog"

export function TablesSidebar({ workspaceId }: { workspaceId: string }) {
  const router = useRouter()
  const { tables, tablesIsLoading, tablesError } = useListTables({
    workspaceId,
  })
  const [showTableDialog, setShowTableDialog] = useState(false)
  const { tableId: selectedTableId } = useParams<{ tableId?: string }>()

  if (tablesIsLoading || tablesError) return null

  return (
    <div className="shrink-0 overflow-auto rounded-lg text-muted-foreground">
      <div className="sticky top-0 flex items-center justify-between bg-background p-2">
        <h2 className="text-xs font-semibold text-muted-foreground">Tables</h2>
        <div>
          <Button
            variant="ghost"
            size="icon"
            className="size-7"
            onClick={() => setShowTableDialog(true)}
          >
            <Plus className="size-4" />
            <span className="sr-only">Add table</span>
          </Button>
        </div>
      </div>
      <div className="flex h-full flex-col space-y-1 p-2">
        {tables && tables.length > 0 ? (
          tables.map((table) => (
            <TableItem
              key={table.id}
              table={table}
              selectedTableId={selectedTableId}
              onSelect={() => {
                router.push(`/workspaces/${workspaceId}/tables/${table.id}`)
              }}
            />
          ))
        ) : (
          <div className="flex aspect-square h-full items-center justify-center rounded-lg border border-dashed border-muted-foreground/25 bg-muted-foreground/5 p-8">
            <div className="text-center text-xs text-muted-foreground/60">
              Create a table to get started
            </div>
          </div>
        )}
      </div>
      <CreateTableDialog
        open={showTableDialog}
        onOpenChange={setShowTableDialog}
      />
    </div>
  )
}
type TableSideNavActionType = "edit" | "delete" | null

function TableItem({
  table,
  selectedTableId,
  onSelect,
}: {
  table: TableReadMinimal
  selectedTableId?: string
  onSelect: (table: TableReadMinimal) => void
}) {
  const { user } = useAuth()
  const [isDropdownOpen, setIsDropdownOpen] = useState(false)
  const [activeType, setActiveType] = useState<TableSideNavActionType>(null)
  const onOpenChange = () => setActiveType(null)
  const isSelected = selectedTableId === table.id
  const isPrivileged = userIsPrivileged(user)
  return (
    <>
      <DropdownMenu open={isDropdownOpen} onOpenChange={setIsDropdownOpen}>
        <div className="group relative">
          <div
            onClick={() => onSelect(table)}
            className={cn(
              "flex w-full items-center gap-2 rounded-md px-2 py-1 text-sm hover:cursor-pointer hover:bg-accent hover:text-accent-foreground",
              (isSelected || isDropdownOpen) &&
                "bg-accent text-accent-foreground"
            )}
          >
            <Table2Icon
              className={cn(
                "mr-2 size-3.5 shrink-0 text-muted-foreground/70",
                isSelected && "text-accent-foreground"
              )}
            />
            <span className="min-w-0 flex-1 truncate">{table.name}</span>
            <DropdownMenuTrigger asChild>
              <Button
                variant="ghost"
                className={cn(
                  "absolute right-2 top-1/2 -translate-y-1/2 rounded-full p-1",
                  "opacity-0 group-hover:opacity-100",
                  isDropdownOpen && "opacity-100",
                  "hover:bg-transparent focus:ring-0 focus-visible:ring-0"
                )}
                onClick={(e) => e.stopPropagation()}
              >
                <EllipsisIcon className="size-3 text-muted-foreground" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start" side="right">
              <DropdownMenuItem
                onClick={(e) => {
                  e.stopPropagation()
                  navigator.clipboard.writeText(table.name)
                }}
                className="py-1 text-xs text-foreground/80"
              >
                <CopyIcon className="mr-2 size-3 group-hover/item:text-accent-foreground" />
                Copy name
              </DropdownMenuItem>
              {isPrivileged && (
                <>
                  <DropdownMenuItem
                    onClick={(e) => {
                      e.stopPropagation()
                      setActiveType("edit")
                    }}
                    className="py-1 text-xs text-foreground/80"
                  >
                    <PencilIcon className="mr-2 size-3 group-hover/item:text-accent-foreground" />
                    Edit
                  </DropdownMenuItem>
                  <DropdownMenuItem
                    className="py-1 text-xs text-destructive/80"
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
          </div>
        </div>
      </DropdownMenu>
      <TableEditDialog
        table={table}
        open={activeType === "edit"}
        onOpenChange={onOpenChange}
      />
      <DeleteTableDialog
        table={table}
        open={activeType === "delete"}
        onOpenChange={onOpenChange}
      />
    </>
  )
}
