"use client"

import { BoxIcon, Check, ChevronsUpDown, Loader2 } from "lucide-react"
import { useEffect, useMemo, useState } from "react"
import type { AgentPresetReadMinimal } from "@/client"
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

interface AgentPresetMenuProps {
  label: string
  presets?: AgentPresetReadMinimal[]
  presetsIsLoading: boolean
  presetsError: unknown
  selectedPresetId: string | null
  onSelect: (presetId: string | null) => void | Promise<void>
  disabled?: boolean
  showSpinner?: boolean
  noPresetDescription?: string
}

export function AgentPresetMenu({
  label,
  presets,
  presetsIsLoading,
  presetsError,
  selectedPresetId,
  onSelect,
  disabled = false,
  showSpinner = false,
  noPresetDescription = "Use workspace default agent instructions.",
}: AgentPresetMenuProps) {
  const [open, setOpen] = useState(false)

  useEffect(() => {
    if (disabled) {
      setOpen(false)
    }
  }, [disabled])

  const presetList = presets ?? []
  const hasPresetOptions = presetList.length > 0
  const errorMessage = useMemo(() => {
    if (typeof presetsError === "string") return presetsError
    if (
      presetsError &&
      typeof presetsError === "object" &&
      "body" in presetsError &&
      typeof (presetsError as { body?: { detail?: unknown } }).body?.detail ===
        "string"
    ) {
      return (presetsError as { body?: { detail?: string } }).body?.detail
    }
    if (
      presetsError &&
      typeof presetsError === "object" &&
      "message" in presetsError &&
      typeof (presetsError as { message?: unknown }).message === "string"
    ) {
      return (presetsError as { message: string }).message
    }
    return "Failed to load presets"
  }, [presetsError])

  const handleSelect = (presetId: string | null) => {
    setOpen(false)
    void onSelect(presetId)
  }

  return (
    <Popover
      open={open}
      onOpenChange={(nextOpen) => {
        if (disabled) return
        setOpen(nextOpen)
      }}
    >
      <PopoverTrigger asChild>
        <Button
          size="sm"
          variant="ghost"
          className="px-2 justify-between"
          role="combobox"
          aria-expanded={open}
          disabled={disabled}
        >
          <div className="flex items-center gap-1.5">
            <BoxIcon className="size-3 text-muted-foreground" />
            <span className="max-w-[11rem] truncate" title={label}>
              {label}
            </span>
          </div>
          {showSpinner ? (
            <Loader2 className="ml-1 size-3 animate-spin text-muted-foreground" />
          ) : (
            <ChevronsUpDown className="ml-1 size-3 opacity-70" />
          )}
        </Button>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-80 p-0">
        {presetsIsLoading ? (
          <div className="flex items-center gap-2 p-3 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            Loading presets…
          </div>
        ) : presetsError ? (
          <div className="p-3 text-sm text-red-600">{errorMessage}</div>
        ) : (
          <Command>
            <CommandInput placeholder="Search presets..." className="h-9" />
            <CommandList className="max-h-64 overflow-y-auto">
              <CommandEmpty>No presets found.</CommandEmpty>
              <CommandGroup>
                <CommandItem
                  value="no preset"
                  onSelect={() => handleSelect(null)}
                  className="flex items-start justify-between gap-2 py-2"
                >
                  <div className="flex flex-col">
                    <span className="text-sm font-medium">No preset</span>
                    <span className="text-xs text-muted-foreground">
                      {noPresetDescription}
                    </span>
                  </div>
                  {selectedPresetId === null ? (
                    <Check className="mt-1 size-4" />
                  ) : null}
                </CommandItem>
                {hasPresetOptions ? (
                  presetList.map((preset) => (
                    <CommandItem
                      key={preset.id}
                      value={`${preset.name} ${preset.description ?? ""}`}
                      onSelect={() => handleSelect(preset.id)}
                      className="flex items-start justify-between gap-2 py-2"
                    >
                      <div className="flex min-w-0 flex-col">
                        <span className="truncate text-sm font-medium">
                          {preset.name}
                        </span>
                        {preset.description ? (
                          <span className="text-xs text-muted-foreground">
                            {preset.description}
                          </span>
                        ) : null}
                      </div>
                      {selectedPresetId === preset.id ? (
                        <Check className="mt-1 size-4" />
                      ) : null}
                    </CommandItem>
                  ))
                ) : (
                  <CommandItem
                    disabled
                    value="no presets available"
                    className="py-2 text-xs text-muted-foreground"
                  >
                    No presets available
                  </CommandItem>
                )}
              </CommandGroup>
            </CommandList>
          </Command>
        )}
      </PopoverContent>
    </Popover>
  )
}
