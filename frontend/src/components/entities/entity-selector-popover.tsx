"use client"

import { Plus } from "lucide-react"
import { useState } from "react"
import type { EntityRead } from "@/client"
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
import { getIconByName } from "@/lib/icons"

interface EntitySelectorPopoverProps {
  entities: EntityRead[] | undefined
  onSelect: (entity: EntityRead) => void
  buttonText?: string
  buttonSize?: "default" | "sm" | "lg" | "icon"
  buttonVariant?:
    | "default"
    | "outline"
    | "ghost"
    | "secondary"
    | "destructive"
    | "link"
  buttonClassName?: string
  align?: "start" | "center" | "end"
  placeholder?: string
  emptyMessage?: string
}

export function EntitySelectorPopover({
  entities,
  onSelect,
  buttonText = "Add record",
  buttonSize = "sm",
  buttonVariant = "outline",
  buttonClassName = "h-7 bg-white",
  align = "end",
  placeholder = "Search entities...",
  emptyMessage = "No entity found.",
}: EntitySelectorPopoverProps) {
  const [open, setOpen] = useState(false)

  const handleSelect = (entity: EntityRead) => {
    setOpen(false)
    onSelect(entity)
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          size={buttonSize}
          variant={buttonVariant}
          className={buttonClassName}
        >
          <Plus className="mr-1.5 h-3.5 w-3.5" />
          {buttonText}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[300px] p-0" align={align}>
        <Command>
          <CommandInput placeholder={placeholder} className="h-9" />
          <CommandEmpty>{emptyMessage}</CommandEmpty>
          <CommandList>
            <CommandGroup>
              {entities?.map((entity: EntityRead) => {
                const IconComponent = entity.icon
                  ? getIconByName(entity.icon)
                  : null
                return (
                  <CommandItem
                    key={entity.id}
                    value={entity.display_name}
                    onSelect={() => handleSelect(entity)}
                    className="flex items-center gap-2"
                  >
                    {IconComponent && (
                      <IconComponent className="h-3.5 w-3.5 text-muted-foreground" />
                    )}
                    <span>{entity.display_name}</span>
                    {entity.key && (
                      <span className="ml-auto text-xs text-muted-foreground">
                        {entity.key}
                      </span>
                    )}
                  </CommandItem>
                )
              })}
            </CommandGroup>
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
