"use client"

import { useState } from "react"
import {
  BetweenHorizonalStartIcon,
  BetweenVerticalStartIcon,
  ChevronDownIcon,
  FileUpIcon,
} from "lucide-react"

import { Button } from "@/components/ui/button"
import { Dialog, DialogContent } from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { TableInsertColumnDialog } from "@/components/tables/table-insert-column-dialog"
import { TableInsertRowDialog } from "@/components/tables/table-insert-row-dialog"

type DialogType = "row" | "column" | "csv" | null

export function TableInsertButton() {
  const [activeDialog, setActiveDialog] = useState<DialogType>(null)

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            role="combobox"
            className="h-7 items-center space-x-1 bg-emerald-500/80 px-3 py-1 text-xs text-white shadow-sm hover:border-emerald-500 hover:bg-emerald-400/80"
          >
            <ChevronDownIcon className="size-3" />
            <span>Insert</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end">
          <DropdownMenuItem
            className="flex items-center gap-2"
            onSelect={() => setActiveDialog("row")}
          >
            <BetweenHorizonalStartIcon className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>Insert Row</span>
              <span className="text-xs text-muted-foreground">
                Insert a new row into the table
              </span>
            </div>
          </DropdownMenuItem>
          <DropdownMenuItem
            className="flex items-center gap-2"
            onSelect={() => setActiveDialog("column")}
          >
            <BetweenVerticalStartIcon className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>Insert Column</span>
              <span className="text-xs text-muted-foreground">
                Insert a new column into the table
              </span>
            </div>
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem
            className="flex items-center gap-2"
            onSelect={() => setActiveDialog("csv")}
          >
            <FileUpIcon className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>Add Rows from CSV</span>
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

      <Dialog
        open={activeDialog === "csv"}
        onOpenChange={() => setActiveDialog(null)}
      >
        <DialogContent>
          <div>CSV</div>
        </DialogContent>
      </Dialog>
    </>
  )
}
