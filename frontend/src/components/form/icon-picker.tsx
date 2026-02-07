"use client"

import { Box, Check, Search } from "lucide-react"
import dynamicIconImports from "lucide-react/dynamicIconImports"
import { useCallback, useMemo, useState } from "react"
import {
  DynamicLucideIcon,
  resolveIconName,
} from "@/components/dynamic-lucide-icon"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"

/** Convert kebab-case to display name: "shield-check" -> "Shield Check" */
function toDisplayName(kebab: string): string {
  return kebab
    .split("-")
    .map((s) => s.charAt(0).toUpperCase() + s.slice(1))
    .join(" ")
}

interface IconEntry {
  iconName: string
  displayName: string
}

/** Build flat list of all dynamic lucide icon names once */
const allIcons: IconEntry[] = (() => {
  const entries = Object.keys(dynamicIconImports).map((iconName) => ({
    iconName,
    displayName: toDisplayName(iconName),
  }))
  entries.sort((a, b) => a.iconName.localeCompare(b.iconName))
  return entries
})()

export interface IconPickerProps {
  value?: string
  onValueChange?: (value: string) => void
  placeholder?: string
  className?: string
}

export function IconPicker({
  value,
  onValueChange,
  placeholder = "Select icon",
  className,
}: IconPickerProps) {
  const [open, setOpen] = useState(false)
  const [searchTerm, setSearchTerm] = useState("")

  const selectedIconName = useMemo(() => {
    if (!value) return null
    return resolveIconName(value)
  }, [value])

  const MAX_VISIBLE_ICONS = 200

  const filteredIcons = useMemo(() => {
    const term = searchTerm.toLowerCase()
    if (!term) return allIcons

    return allIcons.filter(
      (icon) =>
        icon.iconName.includes(term) ||
        icon.displayName.toLowerCase().includes(term)
    )
  }, [searchTerm])

  const visibleIcons = useMemo(
    () =>
      searchTerm ? filteredIcons : filteredIcons.slice(0, MAX_VISIBLE_ICONS),
    [filteredIcons, searchTerm]
  )

  const isTruncated = filteredIcons.length > MAX_VISIBLE_ICONS && !searchTerm

  const handleSelect = useCallback(
    (iconName: string) => {
      onValueChange?.(iconName)
      setOpen(false)
      setSearchTerm("")
    },
    [onValueChange]
  )

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          size="icon"
          aria-expanded={open}
          aria-label={placeholder}
          className={cn("size-9 shrink-0", className)}
        >
          {selectedIconName ? (
            <DynamicLucideIcon name={selectedIconName} className="h-4 w-4" />
          ) : (
            <Box className="h-4 w-4 text-muted-foreground/50" />
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[340px] p-0" align="start" portal={true}>
        {open && (
          <>
            <div className="flex items-center border-b px-3">
              <Search className="mr-2 h-4 w-4 shrink-0 opacity-50" />
              <Input
                placeholder="Search icons..."
                value={searchTerm}
                onChange={(e) => setSearchTerm(e.target.value)}
                className="h-10 border-0 focus:ring-0 focus-visible:ring-0 focus-visible:ring-offset-0"
              />
            </div>
            <div className="h-[300px] overflow-y-auto overscroll-contain">
              {visibleIcons.length === 0 ? (
                <div className="py-6 text-center text-sm text-muted-foreground">
                  No icons found
                </div>
              ) : (
                <div className="grid grid-cols-7 gap-1 p-2">
                  {visibleIcons.map((icon) => {
                    const isSelected = selectedIconName === icon.iconName
                    return (
                      <button
                        key={icon.iconName}
                        type="button"
                        onClick={() => handleSelect(icon.iconName)}
                        className={cn(
                          "relative flex h-9 w-9 items-center justify-center rounded-md border border-transparent text-sm transition-colors hover:bg-accent hover:text-accent-foreground",
                          isSelected && "border-primary bg-accent"
                        )}
                        title={icon.displayName}
                      >
                        <DynamicLucideIcon
                          name={icon.iconName}
                          className="h-4 w-4"
                        />
                        {isSelected && (
                          <div className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-primary">
                            <Check className="h-3 w-3 text-primary-foreground" />
                          </div>
                        )}
                      </button>
                    )
                  })}
                </div>
              )}
              {isTruncated && (
                <div className="px-2 pb-2 text-center text-xs text-muted-foreground">
                  Type to search for more icons
                </div>
              )}
            </div>
            {value && (
              <div className="border-t p-2">
                <Button
                  variant="ghost"
                  size="sm"
                  className="w-full justify-start text-xs"
                  onClick={() => handleSelect("")}
                >
                  Clear selection
                </Button>
              </div>
            )}
          </>
        )}
      </PopoverContent>
    </Popover>
  )
}
