"use client"

import type { Tag as _Tag, TagInputProps } from "emblor"
import { TagInput as EmblorTagInput } from "emblor"
import fuzzysort from "fuzzysort"
import { ChevronDown, X } from "lucide-react"
import type React from "react"
import { useCallback, useMemo, useRef, useState } from "react"
import { Badge } from "@/components/ui/badge"
import {
  Command,
  CommandEmpty,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"

export type Tag = _Tag
// Define the props we want to expose to consumers
type CustomTagInputProps = Omit<
  TagInputProps,
  "activeTagIndex" | "setActiveTagIndex" | "styleClasses"
>

/**
 * Wrapper component for Emblor's TagInput that internally manages activeTagIndex state
 * and applies consistent styling across the application
 */
export function CustomTagInput(props: CustomTagInputProps) {
  const [activeTagIndex, setActiveTagIndex] = useState<number | null>(null)

  return (
    <EmblorTagInput
      {...props}
      activeTagIndex={activeTagIndex}
      setActiveTagIndex={setActiveTagIndex}
      styleClasses={{
        input: "shadow-none text-xs",
        // Allow chips to wrap onto new lines and avoid fixed height to prevent spillover
        inlineTagsContainer:
          "shadow-sm min-h-9 flex flex-wrap items-center gap-1 border text-xs",
        tag: {
          body: "h-5 text-xs border-[0.5px]",
          closeButton: "px-1.5",
        },
      }}
    />
  )
}

export interface Suggestion {
  id: string
  label: string
  value: string
  description?: string
  group?: string
  icon?: React.ReactNode
}

export interface MultiTagCommandInputProps {
  value?: string | string[]
  onChange?: (value: string[]) => void
  suggestions?: Suggestion[]
  placeholder?: string
  className?: string
  disabled?: boolean
  maxTags?: number
  searchKeys: (keyof Suggestion)[]
  /**
   * Allow users to add custom tags by pressing Enter, even if not in suggestions
   */
  allowCustomTags?: boolean
  /**
   * Disable the suggestions dropdown and arrow indicator
   */
  disableSuggestions?: boolean
}

export function MultiTagCommandInput({
  value: valueProp,
  onChange,
  suggestions = [],
  placeholder = "Add tags...",
  className,
  disabled = false,
  maxTags,
  searchKeys,
  allowCustomTags = false,
  disableSuggestions = false,
}: MultiTagCommandInputProps) {
  const [open, setOpen] = useState(false)
  const [inputValue, setInputValue] = useState("")
  const inputRef = useRef<HTMLInputElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [highlightedIndex, setHighlightedIndex] = useState(-1)
  const value = useMemo(() => {
    if (typeof valueProp === "string") {
      return [valueProp]
    }
    return valueProp || []
  }, [valueProp])
  const valueSet = useMemo(() => new Set(value), [value])

  // Convert values to tag objects
  const tags = useMemo(() => {
    return (
      value.map((val, index) => {
        const suggestion = suggestions.find((s) => s.value === val)
        return {
          id: `${index}`,
          text: suggestion?.label || val,
          value: val,
          icon: suggestion?.icon,
        }
      }) || []
    )
  }, [value, suggestions])
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
  // Filter suggestions based on input and exclude already selected
  const filteredSuggestions = useMemo(() => {
    // filter out suggestions that are already selected
    const filtered = suggestions.filter((s) => !valueSet.has(s.value))
    return filterActions(filtered, inputValue).map((result) => result.obj)
  }, [suggestions, inputValue, valueSet, filterActions])

  const handleSelect = (suggestionValue: string) => {
    if (maxTags && value.length >= maxTags) return

    const newValue = [...value, suggestionValue]
    onChange?.(newValue)
    setInputValue("")
    setHighlightedIndex(-1)

    // Keep dropdown open and focused for multiple selections
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  const handleRemoveTag = (tagValue: string) => {
    const newValue = value.filter((v) => v !== tagValue)
    onChange?.(newValue)
  }

  const handleInputChange = (val: string) => {
    setInputValue(val)
    setHighlightedIndex(-1) // Reset highlight when typing
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter") {
      // Always prevent default form submission when Enter is pressed
      e.preventDefault()
      e.stopPropagation()

      if (allowCustomTags && inputValue.trim()) {
        // Don't add if it already exists or if we've hit the max
        if (
          valueSet.has(inputValue.trim()) ||
          (maxTags && value.length >= maxTags)
        ) {
          return
        }

        const newValue = [...value, inputValue.trim()]
        onChange?.(newValue)
        setInputValue("")
        setHighlightedIndex(-1)
      }
    }
  }

  const handleFocus = () => {
    if (!disableSuggestions) {
      setOpen(true) // Only show dropdown when suggestions are enabled
      // Set to first item if there are suggestions, otherwise -1
      setHighlightedIndex(filteredSuggestions.length > 0 ? 0 : -1)
    }
  }

  const handleBlur = () => {
    setOpen(false)
  }

  return (
    <div className={cn("relative", className)} ref={containerRef}>
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverAnchor asChild>
          <div
            className={cn(
              "flex min-h-10 w-full flex-wrap items-center gap-1 rounded-md border border-input bg-background px-3 py-2 text-sm shadow-sm ring-offset-background",
              "focus-within:ring-2 focus-within:ring-ring focus-within:ring-offset-2",
              disabled && "cursor-not-allowed opacity-50",
              className
            )}
            onClick={() => inputRef.current?.focus()}
          >
            {/* Render tags */}
            {tags.map((tag) => (
              <Badge
                key={tag.id}
                variant="secondary"
                className="gap-1 pr-1 text-xs"
              >
                {tag.icon ? (
                  <span className="flex items-center gap-1">
                    <span className="flex items-center justify-center rounded-sm bg-transparent">
                      {tag.icon}
                    </span>
                    <span>{tag.text}</span>
                  </span>
                ) : (
                  tag.text
                )}
                {!disabled && (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation()
                      handleRemoveTag(tag.value)
                    }}
                    className="ml-1 rounded-full hover:bg-muted-foreground/20"
                  >
                    <X className="size-3" />
                  </button>
                )}
              </Badge>
            ))}

            {/* Input */}
            <input
              ref={inputRef}
              type="text"
              value={inputValue}
              onChange={(e) => handleInputChange(e.target.value)}
              onKeyDown={handleKeyDown}
              onFocus={handleFocus}
              onBlur={handleBlur}
              disabled={disabled}
              placeholder={tags.length === 0 ? placeholder : ""}
              className="min-w-[120px] flex-1 bg-transparent text-xs outline-none placeholder:text-muted-foreground"
            />

            {/* Dropdown indicator */}
            {open && <ChevronDown className="size-4 text-muted-foreground" />}
          </div>
        </PopoverAnchor>

        <PopoverContent
          className="mt-1 w-[var(--radix-popover-trigger-width)] p-0"
          align="start"
          onOpenAutoFocus={(e) => e.preventDefault()}
        >
          <Command
            shouldFilter={false}
            onKeyDown={(e) => {
              // Prevent Command from handling arrow keys and enter
              if (["ArrowDown", "ArrowUp", "Enter"].includes(e.key)) {
                e.preventDefault()
                e.stopPropagation()
              }
            }}
          >
            <ScrollArea className="max-h-[300px]">
              <CommandList>
                <CommandEmpty>No suggestions found.</CommandEmpty>
                {filteredSuggestions.map((suggestion, index) => {
                  return (
                    <CommandItem
                      key={suggestion.id}
                      value={suggestion.value}
                      onSelect={() => handleSelect(suggestion.value)}
                      data-suggestion-index={index}
                      className={cn(
                        "flex cursor-pointer gap-2",
                        index === highlightedIndex &&
                          "bg-accent text-accent-foreground"
                      )}
                    >
                      {suggestion.icon && (
                        <div className="flex items-center gap-2">
                          {suggestion.icon}
                        </div>
                      )}
                      <div className="flex flex-col gap-1">
                        <span className="font-medium">
                          {suggestion.label}{" "}
                          {suggestion.group && (
                            <span className="text-xs text-muted-foreground">
                              {suggestion.group}
                            </span>
                          )}
                        </span>
                        {suggestion.description && (
                          <span className="text-xs text-muted-foreground">
                            {suggestion.description}
                          </span>
                        )}
                      </div>
                    </CommandItem>
                  )
                })}
              </CommandList>
            </ScrollArea>
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  )
}
