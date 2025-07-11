import { useState } from "react"
import { HexColorPicker } from "react-colorful"

import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"

export const ColorPicker = ({
  value = "#aabbcc",
  onChange,
  showInput = false,
}: {
  value: string
  onChange: (color: string) => void
  showInput?: boolean
}) => {
  const [isOpen, setIsOpen] = useState(false)

  return (
    <div className="flex items-center gap-2">
      <Popover open={isOpen} onOpenChange={setIsOpen}>
        <PopoverTrigger asChild>
          <button
            className="size-6 rounded border border-gray-200 shadow-sm"
            style={{ backgroundColor: value }}
            aria-label="Pick a color"
          />
        </PopoverTrigger>
        <PopoverContent className="w-auto">
          <HexColorPicker
            color={value}
            onChange={(color: string) => onChange?.(color)}
          />
        </PopoverContent>
      </Popover>
      {showInput && <div className="text-sm text-gray-600">{value}</div>}
    </div>
  )
}
