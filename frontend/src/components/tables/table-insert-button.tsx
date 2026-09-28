"use client"

import {
  BetweenHorizonalStartIcon,
  BetweenVerticalStartIcon,
  ChevronDownIcon,
  FileUpIcon,
  Plus,
} from "lucide-react"
import { useState } from "react"
import { TableImportCsvDialog } from "@/components/tables/table-import-csv-dialog"
import { TableInsertColumnDialog } from "@/components/tables/table-insert-column-dialog"
import { TableInsertRowDialog } from "@/components/tables/table-insert-row-dialog"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

type DialogType = "row" | "column" | "csv" | null

export function TableInsertButton() {
  const [activeDialog, setActiveDialog] = useState<DialogType>(null)

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm" className="h-7 bg-background">
            <Plus className="mr-1 h-3.5 w-3.5" />
            Insert
            <ChevronDownIcon className="ml-1 h-3.5 w-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent
          className="
            [&_[data-radix-collection-item]]:flex
            [&_[data-radix-collection-item]]:items-center
            [&_[data-radix-collection-item]]:gap-2
          "
        >
          <DropdownMenuItem onSelect={() => setActiveDialog("row")}>
            <BetweenHorizonalStartIcon className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>Insert row</span>
              <span className="text-xs text-muted-foreground">
                Insert a new row into the table
              </span>
            </div>
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setActiveDialog("column")}>
            <BetweenVerticalStartIcon className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>Insert column</span>
              <span className="text-xs text-muted-foreground">
                Insert a new column into the table
              </span>
            </div>
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setActiveDialog("csv")}>
            <FileUpIcon className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>Add rows from CSV</span>
              <span className="text-xs text-muted-foreground">
                Add rows from a CSV file
              </span>
            </div>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      <TableInsertRowDialog
        open={activeDialog === "row"}
        onOpenChange={() => setActiveDialog(null)}
      />

      <TableInsertColumnDialog
        open={activeDialog === "column"}
        onOpenChange={() => setActiveDialog(null)}
      />

      <TableImportCsvDialog
        open={activeDialog === "csv"}
        onOpenChange={() => setActiveDialog(null)}
      />
    </>
  )
}
