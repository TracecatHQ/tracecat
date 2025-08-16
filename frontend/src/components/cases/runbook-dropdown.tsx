"use client"

import { formatDistanceToNow } from "date-fns"
import { Calendar, CirclePlay } from "lucide-react"
import { useState } from "react"
import type { PromptRead } from "@/client"
import { RunbookExecuteDialog } from "@/components/cases/runbook-execute-dialog"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip"
import { useListPrompts } from "@/hooks/use-prompt"
import { capitalizeFirst } from "@/lib/utils"

interface RunbookDropdownProps {
  workspaceId: string
  entityType: "case"
  entityId: string
  disabled?: boolean
}

export function RunbookDropdown({
  workspaceId,
  entityType,
  entityId,
  disabled,
}: RunbookDropdownProps) {
  const [open, setOpen] = useState(false)
  const [searchTerm, setSearchTerm] = useState("")
  const [selectedPrompt, setSelectedPrompt] = useState<PromptRead | null>(null)
  const [dialogOpen, setDialogOpen] = useState(false)

  const {
    data: prompts,
    isLoading: promptsLoading,
    error: promptsError,
  } = useListPrompts({ workspaceId })

  const filteredPrompts =
    prompts?.filter(
      (prompt) =>
        prompt.title.toLowerCase().includes(searchTerm.toLowerCase()) ||
        prompt.content.toLowerCase().includes(searchTerm.toLowerCase())
    ) || []

  const handleSelectRunbook = (prompt: PromptRead) => {
    setSelectedPrompt(prompt)
    setDialogOpen(true)
    setOpen(false)
    setSearchTerm("")
  }

  return (
    <>
      <TooltipProvider delayDuration={0}>
        <Tooltip>
          <Popover open={open} onOpenChange={setOpen}>
            <TooltipTrigger asChild>
              <PopoverTrigger asChild>
                <Button
                  size="sm"
                  variant="ghost"
                  className="size-6 p-0"
                  disabled={disabled}
                >
                  <CirclePlay className="h-4 w-4" />
                </Button>
              </PopoverTrigger>
            </TooltipTrigger>
            <TooltipContent side="bottom">Execute runbook</TooltipContent>
            <PopoverContent className="w-96 p-0" align="start">
              <Command>
                <CommandInput
                  placeholder="Search runbooks..."
                  value={searchTerm}
                  onValueChange={setSearchTerm}
                  className="h-9"
                />
                <CommandList>
                  {promptsLoading ? (
                    <CommandEmpty>Loading runbooks...</CommandEmpty>
                  ) : promptsError ? (
                    <CommandEmpty>Failed to load runbooks</CommandEmpty>
                  ) : filteredPrompts.length === 0 ? (
                    <CommandEmpty>
                      {searchTerm
                        ? "No runbooks match your search"
                        : "No runbooks available"}
                    </CommandEmpty>
                  ) : (
                    <CommandGroup>
                      {filteredPrompts.map((prompt) => (
                        <CommandItem
                          key={prompt.id}
                          onSelect={() => handleSelectRunbook(prompt)}
                          className="flex flex-col items-start py-2 cursor-pointer"
                        >
                          <div className="flex w-full justify-between items-start">
                            <div className="flex-1 min-w-0">
                              <div className="text-sm font-medium truncate">
                                {prompt.title}
                              </div>
                              <div className="text-xs text-muted-foreground flex items-center gap-1">
                                <Calendar className="h-3 w-3" />
                                {capitalizeFirst(
                                  formatDistanceToNow(
                                    new Date(prompt.created_at),
                                    {
                                      addSuffix: true,
                                    }
                                  )
                                )}
                              </div>
                            </div>
                          </div>
                          {prompt.tools && prompt.tools.length > 0 && (
                            <div className="flex flex-wrap gap-1 mt-1">
                              {prompt.tools.slice(0, 3).map((tool, index) => (
                                <span
                                  key={index}
                                  className="inline-flex items-center rounded-md bg-muted px-1.5 py-0.5 text-xs font-medium text-muted-foreground"
                                >
                                  {tool}
                                </span>
                              ))}
                              {prompt.tools.length > 3 && (
                                <span className="text-xs text-muted-foreground">
                                  +{prompt.tools.length - 3} more
                                </span>
                              )}
                            </div>
                          )}
                        </CommandItem>
                      ))}
                    </CommandGroup>
                  )}
                </CommandList>
              </Command>
            </PopoverContent>
          </Popover>
        </Tooltip>
      </TooltipProvider>

      <RunbookExecuteDialog
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        prompt={selectedPrompt}
        workspaceId={workspaceId}
        entityType={entityType}
        entityId={entityId}
      />
    </>
  )
}
