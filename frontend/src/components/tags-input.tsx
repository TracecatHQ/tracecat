"use client"

import type { Tag as _Tag, TagInputProps } from "emblor"
import { TagInput as EmblorTagInput } from "emblor"
import fuzzysort from "fuzzysort"
import { ChevronDown, X } from "lucide-react"
import type React from "react"
import { useCallback, useMemo, useRef, useState } from "react"
import { LockedFeatureChip } from "@/components/locked-feature-modal"
import { Badge } from "@/components/ui/badge"
import {
  Command,
  CommandEmpty,
  CommandItem,
  CommandList,
} from "@/components/ui/command"
import {
  HoverCard,
  HoverCardContent,
  HoverCardTrigger,
} from "@/components/ui/hover-card"
import { Popover, PopoverAnchor, PopoverContent } from "@/components/ui/popover"
import { ScrollArea } from "@/components/ui/scroll-area"
import { cn } from "@/lib/utils"

export type Tag = _Tag
// Define the props we want to expose to consumers
type CustomTagInputProps = Omit<
  TagInputProps,
  "activeTagIndex" | "setActiveTagIndex" | "setTags" | "styleClasses" | "tags"
> &
  Pick<TagInputProps, "setTags" | "tags">

/**
 * Wrapper component for Emblor's TagInput that internally manages activeTagIndex state
 * and applies consistent styling across the application
 */
export function CustomTagInput({
  tags,
  setTags,
  ...props
}: CustomTagInputProps) {
  const [activeTagIndex, setActiveTagIndex] = useState<number | null>(null)

  return (
    <EmblorTagInput
      {...props}
      tags={tags}
      setTags={setTags}
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
  /**
   * When true, the selected chip shows a hover card with the provider,
   * display name, and description. Omit for values that don't warrant a
   * hover card (raw UUIDs, slugs).
   */
  showHoverCard?: boolean
  /**
   * Optional display name for the selected chip when the dropdown `label`
   * isn't chip-friendly (e.g. a full dotted action id). Falls back to `label`.
   */
  tagLabel?: string
  /**
   * Optional provider/vendor display name for the selected chip's prefix
   * (e.g. "PagerDuty") when the dropdown `group` is a raw namespace. Falls
   * back to `group`.
   */
  tagGroup?: string
  icon?: React.ReactNode
  locked?: boolean
  onSelect?: () => void
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
  // Enter only selects once the user has typed or navigated, so tabbing into
  // the field and pressing Enter doesn't add an arbitrary tag
  const hasNavigatedRef = useRef(false)
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
          text: suggestion?.tagLabel || suggestion?.label || val,
          value: val,
          icon: suggestion?.icon,
          group: suggestion?.tagGroup || suggestion?.group,
          description: suggestion?.description,
          showHoverCard: suggestion?.showHoverCard ?? false,
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

  // Offer an explicit "Add <text>" row so custom tags stay reachable even
  // when the typed text fuzzy-matches existing suggestions
  const customTagText = allowCustomTags ? inputValue.trim() : ""
  const showCustomRow =
    customTagText.length > 0 &&
    !valueSet.has(customTagText) &&
    !filteredSuggestions.some((s) => s.value === customTagText)
  const rowCount = filteredSuggestions.length + (showCustomRow ? 1 : 0)

  const handleSelect = (suggestion: Suggestion) => {
    if (suggestion.locked) {
      suggestion.onSelect?.()
      return
    }

    if (maxTags && value.length >= maxTags) return

    const newValue = [...value, suggestion.value]
    onChange?.(newValue)
    setInputValue("")
    setHighlightedIndex(0)
    hasNavigatedRef.current = false

    // Keep dropdown open and focused for multiple selections
    setTimeout(() => inputRef.current?.focus(), 0)
  }

  const handleAddCustomTag = () => {
    if (!customTagText || valueSet.has(customTagText)) return
    if (maxTags && value.length >= maxTags) return

    const newValue = [...value, customTagText]
    onChange?.(newValue)
    setInputValue("")
    setHighlightedIndex(0)
    hasNavigatedRef.current = false

    setTimeout(() => inputRef.current?.focus(), 0)
  }

  const handleRemoveTag = (tagValue: string) => {
    const newValue = value.filter((v) => v !== tagValue)
    onChange?.(newValue)
  }

  const handleInputChange = (val: string) => {
    setInputValue(val)
    setHighlightedIndex(0) // Highlight the top match when typing
    if (!disableSuggestions) {
      setOpen(true) // Reopen the dropdown if it was dismissed
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown" && open && rowCount > 0) {
      e.preventDefault()
      hasNavigatedRef.current = true
      setHighlightedIndex((index) => Math.min(index + 1, rowCount - 1))
      return
    }

    if (e.key === "ArrowUp" && open && rowCount > 0) {
      e.preventDefault()
      hasNavigatedRef.current = true
      setHighlightedIndex((index) => Math.max(index - 1, 0))
      return
    }

    if (e.key === "Enter") {
      // Always prevent default form submission when Enter is pressed
      e.preventDefault()
      e.stopPropagation()

      const canSelectHighlighted =
        inputValue.trim().length > 0 || hasNavigatedRef.current
      if (
        open &&
        canSelectHighlighted &&
        highlightedIndex >= 0 &&
        highlightedIndex < rowCount
      ) {
        const suggestion = filteredSuggestions[highlightedIndex]
        if (suggestion) {
          handleSelect(suggestion)
        } else {
          handleAddCustomTag()
        }
        return
      }

      handleAddCustomTag()
    }
  }

  const handleFocus = () => {
    if (!disableSuggestions) {
      setOpen(true) // Only show dropdown when suggestions are enabled
      setHighlightedIndex(0)
      hasNavigatedRef.current = false
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
              "focus-within:ring-1 focus-within:ring-inset focus-within:ring-ring",
              disabled && "cursor-not-allowed opacity-50",
              className
            )}
            onClick={() => inputRef.current?.focus()}
          >
            {/* Render tags */}
            {tags.map((tag) => {
              const label = tag.group ? (
                <span>
                  <span className="text-muted-foreground">{tag.group}</span> ·{" "}
                  {tag.text}
                </span>
              ) : (
                <span>{tag.text}</span>
              )
              const badge = (
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
                      {label}
                    </span>
                  ) : (
                    label
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
              )
              // The remove button lives inside the hover card trigger; its
              // click handler still fires and removes the tag.
              if (tag.showHoverCard) {
                return (
                  <HoverCard key={tag.id} openDelay={200}>
                    <HoverCardTrigger asChild>{badge}</HoverCardTrigger>
                    <HoverCardContent
                      className="w-[300px] p-4 text-xs"
                      align="start"
                    >
                      <div className="flex items-center gap-2">
                        {tag.icon}
                        <div className="flex flex-col">
                          {tag.group && (
                            <span className="text-[10px] font-medium uppercase tracking-wider text-muted-foreground">
                              {tag.group}
                            </span>
                          )}
                          <span className="text-sm font-medium">
                            {tag.text}
                          </span>
                        </div>
                      </div>
                      {tag.description && (
                        <p className="mt-2 text-muted-foreground">
                          {tag.description}
                        </p>
                      )}
                    </HoverCardContent>
                  </HoverCard>
                )
              }
              return badge
            })}

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
          onInteractOutside={(e) => {
            // Clicks inside the input container (e.g. repositioning the
            // cursor) must not dismiss the dropdown
            if (
              e.target instanceof Node &&
              containerRef.current?.contains(e.target)
            ) {
              e.preventDefault()
            }
          }}
        >
          <Command
            shouldFilter={false}
            value={`${highlightedIndex}`}
            onValueChange={(nextValue) => {
              const index = Number.parseInt(nextValue, 10)
              if (!Number.isNaN(index)) {
                setHighlightedIndex(index)
              }
            }}
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
                      value={`${index}`}
                      onSelect={() => handleSelect(suggestion)}
                      onMouseDown={(e) => e.preventDefault()}
                      data-suggestion-index={index}
                      className={cn(
                        "flex cursor-pointer gap-2",
                        suggestion.locked && "text-muted-foreground"
                      )}
                    >
                      {suggestion.icon && (
                        <div className="flex items-center gap-2">
                          {suggestion.icon}
                        </div>
                      )}
                      <div className="flex flex-col gap-1">
                        <div className="flex items-center gap-2">
                          <span className="font-medium">
                            {suggestion.label}{" "}
                            {suggestion.group && (
                              <span className="text-xs text-muted-foreground">
                                {suggestion.group}
                              </span>
                            )}
                          </span>
                          {suggestion.locked ? (
                            <LockedFeatureChip className="shrink-0" />
                          ) : null}
                        </div>
                        {suggestion.description && (
                          <span className="text-xs text-muted-foreground">
                            {suggestion.description}
                          </span>
                        )}
                      </div>
                    </CommandItem>
                  )
                })}
                {showCustomRow && (
                  <CommandItem
                    key="__custom-tag__"
                    value={`${filteredSuggestions.length}`}
                    onSelect={handleAddCustomTag}
                    onMouseDown={(e) => e.preventDefault()}
                    className="flex cursor-pointer gap-2"
                  >
                    <span className="text-xs text-muted-foreground">
                      Add &quot;{customTagText}&quot;
                    </span>
                  </CommandItem>
                )}
              </CommandList>
            </ScrollArea>
          </Command>
        </PopoverContent>
      </Popover>
    </div>
  )
}
