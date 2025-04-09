"use client"

import { useState } from "react"
import { ChevronDownIcon, FileUpIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

type DialogType = "field" | null

export function CaseTableInsertButton() {
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
        <DropdownMenuContent>
          <DropdownMenuItem
            className="flex items-center gap-2"
            onSelect={() => setActiveDialog("field")}
          >
            <FileUpIcon className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>Add field</span>
              <span className="text-xs text-muted-foreground">
                Add a new field to the case schema
              </span>
            </div>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>

      {/* <TableInsertColumnDialog
        open={activeDialog === "field"}
        onOpenChange={() => setActiveDialog(null)}
      /> */}
    </>
  )
}
