"use client"

import { format } from "date-fns"
import { CalendarIcon } from "lucide-react"
import { useState } from "react"
import { formControlClass } from "@/components/forms/form-styles"
import {
  describedBy,
  type FormWidgetProps,
} from "@/components/forms/widgets/types"
import { Button } from "@/components/ui/button"
import { Calendar } from "@/components/ui/calendar"
import { Input } from "@/components/ui/input"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { cn } from "@/lib/utils"

function parseDatetime(value: unknown): Date | undefined {
  if (typeof value !== "string") {
    return undefined
  }
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? undefined : date
}

/**
 * Date picker with a time input. The requester picks in local time and the
 * widget submits an ISO 8601 instant in UTC.
 */
export function DatetimeWidget({
  id,
  field,
  value,
  onChange,
  onBlur,
  disabled,
  invalid,
}: FormWidgetProps) {
  const [open, setOpen] = useState(false)
  const date = parseDatetime(value)

  function handleOpenChange(nextOpen: boolean) {
    setOpen(nextOpen)
    if (!nextOpen) {
      onBlur()
    }
  }

  function handleDateSelect(selected: Date | undefined) {
    if (!selected) {
      onChange(null)
    } else {
      // Keep the time already chosen; a first pick starts at midnight.
      const next = new Date(selected)
      next.setHours(date?.getHours() ?? 0, date?.getMinutes() ?? 0, 0, 0)
      onChange(next.toISOString())
    }
    handleOpenChange(false)
  }

  function handleTimeChange(time: string) {
    const [hours, minutes] = time.split(":").map(Number)
    if (!date || Number.isNaN(hours) || Number.isNaN(minutes)) {
      return
    }
    const next = new Date(date)
    next.setHours(hours, minutes, 0, 0)
    onChange(next.toISOString())
  }

  return (
    <div className="flex gap-2">
      <Popover open={open} onOpenChange={handleOpenChange}>
        <PopoverTrigger asChild>
          <Button
            id={id}
            type="button"
            variant="outline"
            disabled={disabled}
            aria-invalid={invalid}
            aria-describedby={describedBy(id, field, invalid)}
            className={formControlClass(
              invalid,
              cn(
                "min-w-0 flex-1 justify-start gap-2 px-3 font-normal",
                !date && "text-muted-foreground"
              )
            )}
          >
            <CalendarIcon className="size-4 text-muted-foreground" />
            {date
              ? format(date, "MMM d, yyyy")
              : (field.placeholder ?? "Pick a date")}
          </Button>
        </PopoverTrigger>
        <PopoverContent className="w-auto overflow-hidden p-0" align="start">
          <Calendar
            mode="single"
            selected={date}
            defaultMonth={date}
            captionLayout="dropdown"
            onSelect={handleDateSelect}
          />
        </PopoverContent>
      </Popover>
      <Input
        type="time"
        aria-label={`${field.label} time`}
        value={date ? format(date, "HH:mm") : ""}
        onChange={(event) => handleTimeChange(event.target.value)}
        onBlur={onBlur}
        disabled={disabled || !date}
        className={formControlClass(
          invalid,
          "w-28 flex-none appearance-none bg-background [&::-webkit-calendar-picker-indicator]:hidden"
        )}
      />
    </div>
  )
}
