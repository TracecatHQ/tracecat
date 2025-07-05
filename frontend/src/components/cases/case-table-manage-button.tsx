"use client"

import { format } from "date-fns"
import { BracesIcon, ChevronDownIcon, CirclePlusIcon } from "lucide-react"
import { useRouter } from "next/navigation"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useCreateCase } from "@/lib/hooks"
import { useWorkspace } from "@/providers/workspace"

export function CaseTableInsertButton() {
  const router = useRouter()
  const { workspaceId } = useWorkspace()

  const { createCase, createCaseIsPending } = useCreateCase(workspaceId)

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
            onSelect={(e) => {
              e.preventDefault()
              createCase({
                summary: `New case - ${format(new Date(), "PPpp")}`,
                description: "",
                status: "unknown",
                priority: "unknown",
                severity: "unknown",
              })
            }}
            disabled={createCaseIsPending}
          >
            <CirclePlusIcon className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>Create case</span>
              <span className="text-xs text-muted-foreground">
                Create a new case
              </span>
            </div>
          </DropdownMenuItem>
          <DropdownMenuItem
            className="flex items-center gap-2"
            onSelect={() =>
              router.push(`/workspaces/${workspaceId}/cases/fields`)
            }
          >
            <BracesIcon className="size-4 text-foreground/80" />
            <div className="flex flex-col text-xs">
              <span>Custom fields</span>
              <span className="text-xs text-muted-foreground">
                Manage custom fields
              </span>
            </div>
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  )
}
