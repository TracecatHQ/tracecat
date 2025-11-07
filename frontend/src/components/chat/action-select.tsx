"use client"

import fuzzysort from "fuzzysort"
import { ChevronDown } from "lucide-react"
import { useCallback, useMemo, useState } from "react"
import type { ControllerRenderProps, FieldValues } from "react-hook-form"
import type { Suggestion } from "@/components/tags-input"
import { Button } from "@/components/ui/button"
import {
  Command,
  CommandEmpty,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"

interface ActionSelectProps<T extends FieldValues> {
  field: ControllerRenderProps<T>
  suggestions: Suggestion[]
  searchKeys?: (keyof Suggestion)[]
  placeholder?: string
  disabled?: boolean
  className?: string
}

/**
 * Single-select action picker with fuzzy search
 * Uses Command + Popover pattern for searchable dropdown
 */
export function ActionSelect<T extends FieldValues>({
  field,
  suggestions,
  searchKeys = ["label", "value", "description", "group"],
  placeholder = "Select an action...",
  disabled = false,
  className,
}: ActionSelectProps<T>) {
  const [open, setOpen] = useState(false)
  const [searchValue, setSearchValue] = useState("")

  // Find the currently selected suggestion
  const selectedSuggestion = useMemo(() => {
    return suggestions.find((s) => s.value === field.value)
  }, [suggestions, field.value])

  // Filter suggestions using fuzzy search
  const filterActions = useCallback(
    (actions: Suggestion[], search: string) => {
      if (!search.trim()) {
        return actions.map((action) => ({ obj: action, score: 0 }))
      }

      const results = fuzzysort.go<Suggestion>(search, actions, {
        all: true,
        keys: searchKeys,
      })
      return results
    },
    [searchKeys]
  )

  const filteredResults = useMemo(() => {
    return filterActions(suggestions, searchValue)
  }, [suggestions, searchValue, filterActions])

  const sortedSuggestions = useMemo(() => {
    return [...filteredResults].sort((a, b) => {
      // If there's a search, sort by fuzzy score first
      if (searchValue.trim()) {
        if (a.score !== b.score) {
          return b.score - a.score // Higher score first
        }
      }
      // Then sort by label
      return a.obj.label.localeCompare(b.obj.label)
    })
  }, [filteredResults, searchValue])

  const handleSelect = (value: string) => {
    field.onChange(value)
    setOpen(false)
    setSearchValue("")
  }

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
          className={cn(
            "w-full justify-between text-left font-normal h-9",
            className
          )}
        >
          <div className="flex items-center gap-2 truncate">
            {selectedSuggestion ? (
              <>
                {selectedSuggestion.icon && (
                  <div className="flex items-center shrink-0">
                    {selectedSuggestion.icon}
                  </div>
                )}
                <span className="truncate">{selectedSuggestion.label}</span>
              </>
            ) : (
              <span className="text-muted-foreground">{placeholder}</span>
            )}
          </div>
          <ChevronDown className="ml-2 size-4 shrink-0 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[400px] p-0" align="start">
        <Command shouldFilter={false}>
          <CommandInput
            placeholder="Search actions..."
            value={searchValue}
            onValueChange={(value) => {
              // First update the value
              setSearchValue(value)
              // Then force scroll to top of the list
              requestAnimationFrame(() => {
                const commandList = document.querySelector("[cmdk-list]")
                if (commandList) {
                  commandList.scrollTop = 0
                }
              })
            }}
            className="h-9"
          />
          <ScrollArea className="max-h-[300px]">
            <CommandList>
              <CommandEmpty>No actions found.</CommandEmpty>
              {sortedSuggestions.map((result) => {
                const suggestion = result.obj
                return (
                  <CommandItem
                    key={suggestion.value}
                    value={suggestion.value}
                    onSelect={() => handleSelect(suggestion.value)}
                    className="cursor-pointer"
                  >
                    <div className="flex items-center gap-2 w-full">
                      {suggestion.icon && (
                        <div className="flex items-center shrink-0">
                          {suggestion.icon}
                        </div>
                      )}
                      <div className="flex flex-col gap-1 min-w-0 flex-1">
                        <span className="font-medium truncate">
                          {suggestion.label}
                          {suggestion.group && (
                            <span className="text-xs text-muted-foreground ml-1">
                              {suggestion.group}
                            </span>
                          )}
                        </span>
                        <span className="text-xs text-muted-foreground truncate">
                          {suggestion.value}
                        </span>
                      </div>
                    </div>
                  </CommandItem>
                )
              })}
            </CommandList>
          </ScrollArea>
        </Command>
      </PopoverContent>
    </Popover>
  )
}
