"use client"

import { Check, Search } from "lucide-react"
import { useCallback, useMemo, useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { ScrollArea } from "@/components/ui/scroll-area"
import { type IconData, iconList } from "@/lib/icons"
import { cn } from "@/lib/utils"

interface IconPickerProps {
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

  const selectedIcon = useMemo(() => {
    if (!value) return null
    return iconList.find((icon) => icon.name === value)
  }, [value])

  const SelectedIconComponent = selectedIcon ? selectedIcon.icon : null

  const filteredIcons = useMemo(() => {
    const term = searchTerm.toLowerCase()
    if (!term) return iconList

    return iconList.filter(
      (icon) =>
        icon.name.toLowerCase().includes(term) ||
        icon.displayName.toLowerCase().includes(term) ||
        icon.category.toLowerCase().includes(term)
    )
  }, [searchTerm])

  const groupedIcons = useMemo(() => {
    const groups: Record<string, IconData[]> = {}
    filteredIcons.forEach((icon) => {
      if (!groups[icon.category]) {
        groups[icon.category] = []
      }
      groups[icon.category].push(icon)
    })
    return groups
  }, [filteredIcons])

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
          role="combobox"
          aria-expanded={open}
          className={cn(
            "w-full justify-start text-left font-normal",
            !value && "text-muted-foreground",
            className
          )}
        >
          {SelectedIconComponent && selectedIcon ? (
            <div className="flex items-center gap-2">
              <SelectedIconComponent className="h-4 w-4" />
              <span>{selectedIcon.displayName}</span>
            </div>
          ) : (
            <span>{placeholder}</span>
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[400px] p-0" align="start">
        <div className="flex items-center border-b px-3">
          <Search className="mr-2 h-4 w-4 shrink-0 opacity-50" />
          <Input
            placeholder="Search icons..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="h-10 border-0 focus:ring-0 focus-visible:ring-0 focus-visible:ring-offset-0"
          />
        </div>
        <ScrollArea className="h-[300px]">
          {Object.keys(groupedIcons).length === 0 ? (
            <div className="py-6 text-center text-sm text-muted-foreground">
              No icons found
            </div>
          ) : (
            <div className="p-2">
              {Object.entries(groupedIcons).map(([category, icons]) => (
                <div key={category} className="mb-4">
                  <div className="px-2 py-1.5 text-xs font-semibold text-muted-foreground">
                    {category}
                  </div>
                  <div className="grid grid-cols-8 gap-1">
                    {icons.map((icon) => {
                      const IconComponent = icon.icon
                      const isSelected = value === icon.name
                      return (
                        <button
                          key={icon.name}
                          type="button"
                          onClick={() => handleSelect(icon.name)}
                          className={cn(
                            "relative flex h-9 w-9 items-center justify-center rounded-md border border-transparent text-sm transition-colors hover:bg-accent hover:text-accent-foreground",
                            isSelected && "border-primary bg-accent"
                          )}
                          title={icon.displayName}
                        >
                          <IconComponent className="h-4 w-4" />
                          {isSelected && (
                            <div className="absolute -right-1 -top-1 flex h-4 w-4 items-center justify-center rounded-full bg-primary">
                              <Check className="h-3 w-3 text-primary-foreground" />
                            </div>
                          )}
                        </button>
                      )
                    })}
                  </div>
                </div>
              ))}
            </div>
          )}
        </ScrollArea>
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
      </PopoverContent>
    </Popover>
  )
}
