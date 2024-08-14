"use client"

import React, { useEffect, useState } from "react"
import { usePathname, useRouter } from "next/navigation"
import { WorkspaceMetadataResponse } from "@/client"
import { useAuth } from "@/providers/auth"
import { CaretSortIcon, CheckIcon } from "@radix-ui/react-icons"
import { PlusCircleIcon } from "lucide-react"

import { useWorkspace, useWorkspaceManager } from "@/lib/hooks"
import { cn } from "@/lib/utils"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"

export function WorkspaceSelector(props: React.HTMLAttributes<HTMLElement>) {
  const { user } = useAuth()
  const isAdmin = user?.is_superuser || user?.role === "admin"
  const { workspaceId } = useWorkspace()
  const {
    workspaces,
    workspacesError,
    workspacesIsLoading,
    setLastWorkspaceId,
  } = useWorkspaceManager()
  const [open, setOpen] = useState(false)
  const [currWorkspace, setCurrWorkspace] = useState<
    WorkspaceMetadataResponse | undefined
  >()
  const pathname = usePathname()
  const router = useRouter()

  useEffect(() => {
    if (workspaceId) {
      setCurrWorkspace(workspaces?.find((ws) => ws.id === workspaceId))
      setLastWorkspaceId(workspaceId)
    }
  }, [workspaceId, workspaces])

  if (workspacesIsLoading) {
    return null
  }
  if (workspacesError) {
    return <div>Error loading workspaces</div>
  }

  return (
    <Popover open={open} onOpenChange={setOpen} {...props}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-label="Load a workspace..."
          aria-expanded={open}
          className="flex-1 justify-between md:min-w-[150px] md:max-w-[200px] lg:min-w-[250px] lg:max-w-[300px]"
        >
          {currWorkspace?.name || "Select a workspace..."}
          <CaretSortIcon className="ml-2 size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[300px] p-0" align="start">
        <Command>
          <CommandInput placeholder="Search workspaces..." />
          <CommandList>
            <CommandEmpty>No workspaces found.</CommandEmpty>
            <CommandGroup heading="My Workspaces">
              {workspaces?.map((ws) => (
                <CommandItem
                  key={ws.id}
                  onSelect={() => {
                    setCurrWorkspace(ws)
                    // replace /workspaces/<ws-id>/... with /workspaces/<new-ws-id>/...
                    const newPath = pathname.replace(
                      /\/workspaces\/[^/]+/,
                      `/workspaces/${ws.id}`
                    )
                    router.push(newPath)
                    setOpen(false)
                  }}
                >
                  {ws.name}
                  <CheckIcon
                    className={cn(
                      "ml-auto size-4",
                      currWorkspace?.id === ws.id ? "opacity-100" : "opacity-0"
                    )}
                  />
                </CommandItem>
              ))}
            </CommandGroup>
            {isAdmin && (
              <>
                <CommandSeparator />
                <CommandGroup heading="Management">
                  <CommandItem
                    className="flex items-center"
                    key="add-workspace"
                    onSelect={() => {
                      setOpen(false)
                    }}
                  >
                    <PlusCircleIcon className="mr-2 size-4" />
                    Add Workspace
                  </CommandItem>
                </CommandGroup>
              </>
            )}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
