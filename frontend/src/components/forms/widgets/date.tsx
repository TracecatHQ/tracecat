"use client"

import { format, parse } from "date-fns"
import { CalendarIcon } from "lucide-react"
import { useState } from "react"
import { formControlClass } from "@/components/forms/form-styles"
import {
  describedBy,
  type FormWidgetProps,
} from "@/components/forms/widgets/types"
import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { isIsoDate } from "@/lib/forms/validate"
import { cn } from "@/lib/utils"

const WIRE_FORMAT = "yyyy-MM-dd"

/** Calendar date picker. Submits `YYYY-MM-DD`, with no time or time zone. */
export function DateWidget({
  id,
  field,
  value,
  onChange,
  onBlur,
  disabled,
  invalid,
}: FormWidgetProps) {
  const [open, setOpen] = useState(false)
  // Parsed as a local date so the day never shifts across time zones.
  const date = isIsoDate(value)
    ? parse(value, WIRE_FORMAT, new Date())
    : undefined

  function handleOpenChange(nextOpen: boolean) {
    setOpen(nextOpen)
    if (!nextOpen) {
      onBlur()
    }
  }

  return (
    <Popover open={open} onOpenChange={handleOpenChange}>
      <PopoverTrigger asChild>
        <Button
          id={id}
          type="button"
          variant="outline"
          disabled={disabled}
          // Named by the field label and the text it shows, so the picked date
          // is announced. The label alone would hide it.
          aria-labelledby={`${id}-label ${id}-value`}
          aria-invalid={invalid}
          aria-describedby={describedBy(id, field, invalid)}
          className={formControlClass(
            invalid,
            cn(
              "w-full justify-start gap-2 px-3 font-normal",
              !date && "text-muted-foreground"
            )
          )}
        >
          <CalendarIcon className="size-4 text-muted-foreground" />
          <span id={`${id}-value`}>
            {date
              ? format(date, "MMM d, yyyy")
              : (field.placeholder ?? "Pick a date")}
          </span>
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-auto overflow-hidden p-0" align="start">
        <Calendar
          mode="single"
          selected={date}
          defaultMonth={date}
          captionLayout="dropdown"
          onSelect={(selected) => {
            onChange(selected ? format(selected, WIRE_FORMAT) : null)
            handleOpenChange(false)
          }}
        />
      </PopoverContent>
    </Popover>
  )
}
