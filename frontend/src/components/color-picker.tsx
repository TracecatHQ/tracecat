import { useState } from "react"
import { HexColorPicker } from "react-colorful"

import { Button } from "@/components/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"

export const ColorPicker = ({
  value = "#aabbcc",
  onChange,
  showInput = false,
  allowEmpty = false,
  resetLabel = "Use default color",
}: {
  value: string
  onChange: (color: string) => void
  showInput?: boolean
  allowEmpty?: boolean
  resetLabel?: string
}) => {
  const [isOpen, setIsOpen] = useState(false)
  const hasColor = value.trim().length > 0
  const pickerColor = hasColor ? value : "#aabbcc"

  return (
    <div className="flex items-center gap-2">
      <Popover open={isOpen} onOpenChange={setIsOpen}>
        <PopoverTrigger asChild>
          <button
            className={cn(
              "flex size-6 items-center justify-center rounded border border-gray-200 shadow-sm",
              !hasColor && "bg-muted text-[9px] text-muted-foreground"
            )}
            style={hasColor ? { backgroundColor: value } : undefined}
            aria-label="Pick a color"
          >
            {!hasColor && "-"}
          </button>
        </PopoverTrigger>
        <PopoverContent className="w-auto space-y-2" portal={true}>
          <HexColorPicker
            color={pickerColor}
            onChange={(color: string) => onChange?.(color)}
          />
          {allowEmpty && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="w-full justify-start px-2 text-xs"
              onClick={() => onChange("")}
            >
              {resetLabel}
            </Button>
          )}
        </PopoverContent>
      </Popover>
      {showInput && (
        <div className="text-sm text-gray-600">{hasColor ? value : "default"}</div>
      )}
    </div>
  )
}
