"use client"

import { useRouter } from "next/navigation"
import { useWorkspace } from "@/providers/workspace"
import { ChevronDownIcon, KeyRoundIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"

export function CaseTableInsertButton() {
  const router = useRouter()
  const { workspaceId } = useWorkspace()

  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button
            role="combobox"
            className="h-7 items-center space-x-1 bg-emerald-500/80 px-3 py-1 text-xs text-white shadow-sm hover:border-emerald-500 hover:bg-emerald-400/80"
          >
            <ChevronDownIcon className="size-3" />
            <span>Manage</span>
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent>
          <DropdownMenuItem
            className="flex items-center gap-2"
            onSelect={() =>
              router.push(`/workspaces/${workspaceId}/cases/fields`)
            }
          >
            <KeyRoundIcon className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>Case Fields</span>
              <span className="text-xs text-muted-foreground">
                Manage case fields schema
              </span>
            </div>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  )
}
